import 'dart:async';
import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../api/api_client.dart';
import '../auth/session.dart';
import '../config.dart';

/// A message from the server hub: {"type": "notification" | "ops" | "boarding", "data": {...}}.
class LiveMessage {
  const LiveMessage(this.type, this.data);

  final String type;
  final Json data;

  /// For `ops` messages: the domain event name, e.g. "TripDelayed".
  String? get event => data['event'] as String?;
  Json get payload => (data['payload'] as Json?) ?? const {};
}

/// Server close code: the token was missing, invalid, expired or revoked.
const closeUnauthorized = 4401;

/// Opens a socket. Swapped out in tests.
typedef ChannelFactory = WebSocketChannel Function(Uri uri);

/// Keeps one WebSocket open while signed in. Reconnects with backoff and
/// re-subscribes to topics. Always uses the *current* access token on (re)connect.
///
/// The token goes in the first frame (`{"action": "auth"}`), never in the URL, so it doesn't end
/// up in server or proxy logs. When the server closes with [closeUnauthorized] (token expired or
/// revoked) the client renews the session through [onUnauthorized] before reconnecting.
class RealtimeClient {
  RealtimeClient(this._token, {this.onUnauthorized, ChannelFactory? connect})
    : _open = connect ?? WebSocketChannel.connect {
    _connect();
  }

  final String? Function() _token;

  /// Renews the session after the server rejected the token (close code [closeUnauthorized]).
  final Future<void> Function()? onUnauthorized;
  final ChannelFactory _open;
  final _out = StreamController<LiveMessage>.broadcast();
  final _topics = <String>{};
  WebSocketChannel? _ch;
  StreamSubscription? _sub;
  Timer? _ping;
  Timer? _retry;
  var _attempt = 0;
  var _closed = false;

  Stream<LiveMessage> get messages => _out.stream;

  void subscribe(String topic) {
    if (_topics.add(topic)) _send({'action': 'subscribe', 'topic': topic});
  }

  void unsubscribe(String topic) {
    if (_topics.remove(topic)) _send({'action': 'unsubscribe', 'topic': topic});
  }

  void _send(Map<String, dynamic> m) {
    try {
      _ch?.sink.add(jsonEncode(m));
    } catch (_) {
      /* reconnect will re-send topics */
    }
  }

  Future<void> _connect() async {
    final token = _token();
    if (_closed || token == null) return;
    try {
      final ch = _open(Uri.parse('${AppConfig.wsBase}/ws'));
      await ch.ready;
      ch.sink.add(jsonEncode({'token': token}));
      _ch = ch;
      _send({'action': 'auth', 'token': token});
      for (final t in _topics) {
        _send({'action': 'subscribe', 'topic': t});
      }
      _sub = ch.stream.listen(
        (raw) {
          final m = jsonDecode(raw as String) as Json;
          final type = m['type'] as String;
          if (type == 'authenticated') {
            _attempt = 0;
          } else if (type != 'pong' && type != 'error') {
            _out.add(LiveMessage(type, (m['data'] as Json?) ?? const {}));
          }
        },
        onDone: () => _onClosed(ch.closeCode),
        onError: (_) => _scheduleReconnect(),
      );
      _ping = Timer.periodic(const Duration(seconds: 25), (_) => _send({'action': 'ping'}));
    } catch (_) {
      _scheduleReconnect();
    }
  }

  Future<void> _onClosed(int? code) async {
    final renew = onUnauthorized;
    if (code == closeUnauthorized && !_closed && renew != null) {
      await renew(); // signs out if the session can't be renewed, which disposes this client
    }
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    _ping?.cancel();
    _sub?.cancel();
    _ch = null;
    if (_closed) return;
    final secs = [1, 2, 4, 8, 15, 20][_attempt.clamp(0, 5)];
    _attempt++;
    _retry?.cancel();
    _retry = Timer(Duration(seconds: secs), _connect);
  }

  void dispose() {
    _closed = true;
    _retry?.cancel();
    _ping?.cancel();
    _sub?.cancel();
    _ch?.sink.close();
    _out.close();
  }
}

/// One client per signed-in user. Token refreshes don't recreate it: the client reads the
/// latest access token on every (re)connect, and the server closes sockets whose token expired.
final realtimeProvider = Provider<RealtimeClient?>((ref) {
  final userId = ref.watch(sessionProvider.select((s) => s?.user.id));
  if (userId == null) return null;
  final client = RealtimeClient(
    () => ref.read(sessionProvider)?.accessToken,
    onUnauthorized: () => ref.read(sessionProvider.notifier).refresh(),
  );
  ref.onDispose(client.dispose);
  return client;
});

final liveMessagesProvider = StreamProvider<LiveMessage>((ref) {
  final client = ref.watch(realtimeProvider);
  return client?.messages ?? const Stream.empty();
});

/// Call from a ConsumerWidget's build: re-run [onChange] when a relevant live message arrives.
void listenLive(WidgetRef ref, void Function(LiveMessage m) onChange, {Set<String>? events}) {
  ref.listen<AsyncValue<LiveMessage>>(liveMessagesProvider, (_, next) {
    final m = next.asData?.value;
    if (m == null) return;
    if (events == null || events.contains(m.event) || (m.type != 'ops' && events.contains(m.type))) {
      onChange(m);
    }
  });
}
