// Fixes from Review.md: realtime auth and token renewal (B3/M2), staying signed in on a flaky
// network (M2), the admin board not refetching on every GPS fix (H3), and error messages users see.
import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:transit/core/api/api_client.dart';
import 'package:transit/core/auth/session.dart';
import 'package:transit/core/realtime/realtime.dart';
import 'package:transit/design/design.dart';
import 'package:transit/modules/auth/screens/login_screen.dart';
import 'package:transit/modules/boarding/screens/student_scan_screen.dart';
import 'package:transit/modules/dashboard/data/dashboard_api.dart';
import 'package:transit/modules/dashboard/screens/admin_board_screen.dart';
import 'package:transit/modules/notifications/data/notifications_api.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'support/fakes.dart';

// ---------------- Fakes ----------------
class _FakeSink implements WebSocketSink {
  _FakeSink(this.sent);

  final List<Json> sent;

  @override
  void add(dynamic data) => sent.add(jsonDecode(data as String) as Json);

  @override
  void addError(Object error, [StackTrace? stackTrace]) {}

  @override
  Future<void> addStream(Stream<dynamic> stream) async {}

  @override
  Future<void> close([int? closeCode, String? closeReason]) async {}

  @override
  Future<void> get done => Future.value();
}

/// A socket the test plays the server for.
class _FakeChannel implements WebSocketChannel {
  _FakeChannel(this.uri);

  final Uri uri;
  final sent = <Json>[];
  final _incoming = StreamController<dynamic>();
  int? _closeCode;

  void serverSends(Json frame) => _incoming.add(jsonEncode(frame));

  void serverCloses(int code) {
    _closeCode = code;
    _incoming.close();
  }

  @override
  int? get closeCode => _closeCode;

  @override
  String? get closeReason => null;

  @override
  String? get protocol => null;

  @override
  Future<void> get ready => Future.value();

  @override
  Stream<dynamic> get stream => _incoming.stream;

  @override
  WebSocketSink get sink => _FakeSink(sent);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _MemoryStore implements SessionStore {
  String? value;

  @override
  Future<String?> read() async => value;

  @override
  Future<void> write(String? v) async => value = v;
}

/// An auth client whose every request fails the way [fail] says.
Dio _failingDio(DioException Function(RequestOptions o) fail) =>
    Dio()..interceptors.add(InterceptorsWrapper(onRequest: (o, h) => h.reject(fail(o))));

DioException _httpError(RequestOptions o, int status, [Json? body]) => DioException(
  requestOptions: o,
  type: DioExceptionType.badResponse,
  response: Response(requestOptions: o, statusCode: status, data: body),
);

const _session = Session(
  accessToken: 'old-access',
  refreshToken: 'refresh',
  user: AppUser(id: 1, email: 'driver1@college.edu', fullName: 'Murugan K', role: Role.driver),
);

void main() {
  late Dio Function() realAuthClient;
  late SessionStore realStore;

  setUp(() {
    realAuthClient = SessionController.authClient;
    realStore = SessionController.store;
    SessionController.store = _MemoryStore();
  });

  tearDown(() {
    SessionController.authClient = realAuthClient;
    SessionController.store = realStore;
    SessionController.restored = null;
  });

  // ---------------- Realtime ----------------
  testWidgets('the socket signs in with its first frame, never the URL', (tester) async {
    final channels = <_FakeChannel>[];
    final client = RealtimeClient(
      () => 'token-1',
      connect: (uri) {
        channels.add(_FakeChannel(uri));
        return channels.last;
      },
    );
    client.subscribe('route:3');
    await tester.pump();

    final ch = channels.single;
    expect(ch.uri.query, isEmpty);
    expect(ch.sent.first, {'action': 'auth', 'token': 'token-1'});
    expect(ch.sent.skip(1), [
      {'action': 'subscribe', 'topic': 'route:3'},
    ]);
    client.dispose();
  });

  testWidgets('a rejected token is renewed before reconnecting', (tester) async {
    final channels = <_FakeChannel>[];
    var token = 'expired';
    var renewals = 0;
    final client = RealtimeClient(
      () => token,
      onUnauthorized: () async {
        renewals++;
        token = 'fresh';
      },
      connect: (uri) {
        channels.add(_FakeChannel(uri));
        return channels.last;
      },
    );
    await tester.pump();
    channels.single.serverCloses(closeUnauthorized);
    await tester.pump();
    await tester.pump(const Duration(seconds: 2)); // reconnect backoff
    expect(renewals, 1);
    expect(channels, hasLength(2));
    expect(channels.last.sent.first, {'action': 'auth', 'token': 'fresh'});
    client.dispose();
  });

  testWidgets('server status frames are not passed on as live messages', (tester) async {
    late _FakeChannel ch;
    final client = RealtimeClient(() => 't', connect: (uri) => ch = _FakeChannel(uri));
    final got = <LiveMessage>[];
    client.messages.listen(got.add);
    await tester.pump();
    ch.serverSends({'type': 'authenticated', 'data': {}});
    ch.serverSends({
      'type': 'error',
      'data': {'topic': 'route:9', 'code': 'forbidden'},
    });
    ch.serverSends({
      'type': 'ops',
      'data': {'event': 'TripStarted', 'payload': {}},
    });
    await tester.pump();
    expect(got.map((m) => m.type), ['ops']);
    client.dispose();
  });

  // ---------------- Session ----------------
  test('a network error while renewing keeps the user signed in', () async {
    SessionController.restored = _session;
    SessionController.authClient = () =>
        _failingDio((o) => DioException(requestOptions: o, type: DioExceptionType.connectionTimeout));
    final container = ProviderContainer();
    addTearDown(container.dispose);
    expect(await container.read(sessionProvider.notifier).refresh(), isNull);
    expect(container.read(sessionProvider), isNotNull);

    SessionController.authClient = () => _failingDio((o) => _httpError(o, 502));
    final container2 = ProviderContainer();
    addTearDown(container2.dispose);
    expect(await container2.read(sessionProvider.notifier).refresh(), isNull);
    expect(container2.read(sessionProvider), isNotNull);
  });

  test('a revoked session signs the user out', () async {
    SessionController.restored = _session;
    final store = SessionController.store as _MemoryStore..value = jsonEncode(_session.toJson());
    SessionController.authClient = () => _failingDio(
      (o) => _httpError(o, 401, {'detail': 'This session has ended. Sign in again.', 'code': 'session_revoked'}),
    );
    final container = ProviderContainer();
    addTearDown(container.dispose);
    expect(await container.read(sessionProvider.notifier).refresh(), isNull);
    expect(container.read(sessionProvider), isNull);
    expect(store.value, isNull);
  });

  testWidgets('login shows the rate-limit message', (tester) async {
    SessionController.authClient = () => _failingDio(
      (o) => _httpError(o, 429, {
        'detail': 'Too many sign-in attempts. Wait a minute and try again.',
        'code': 'rate_limited',
      }),
    );
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(theme: buildTransitTheme(), home: const LoginScreen()),
      ),
    );
    await tester.enterText(find.byType(TextFormField).at(0), 'student4@college.edu');
    await tester.enterText(find.byType(TextFormField).at(1), 'transit123');
    await tester.ensureVisible(find.byType(SignButton));
    await tester.tap(find.byType(SignButton));
    await tester.pumpAndSettle();
    expect(find.text('Too many sign-in attempts. Wait a minute and try again.'), findsOneWidget);
  });

  // ---------------- Admin board ----------------
  test('GPS fixes do not refresh the admin board', () {
    expect(refreshesBoard(const LiveMessage('position', {'trip_id': 1})), isFalse);
    expect(refreshesBoard(const LiveMessage('ops', {'event': 'StopArrived'})), isTrue);
    expect(refreshesBoard(const LiveMessage('notification', {})), isTrue);
  });

  testWidgets('the admin board refetches once for a burst of updates and never for GPS', (tester) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final live = StreamController<LiveMessage>.broadcast();
    addTearDown(live.close);
    var fetches = 0;
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          adminDashboardProvider.overrideWith((ref) async {
            fetches++;
            return AdminDashboard.fromJson(adminDashboardJson());
          }),
          liveMessagesProvider.overrideWith((ref) => live.stream),
          realtimeProvider.overrideWithValue(null),
          unreadCountProvider.overrideWith((ref) async => 0),
        ],
        child: MaterialApp(
          theme: buildTransitTheme(),
          home: const Scaffold(body: AdminBoardScreen()),
        ),
      ),
    );
    await tester.pump();
    expect(fetches, 1);

    for (var i = 0; i < 5; i++) {
      live.add(const LiveMessage('position', {'trip_id': 7}));
      await tester.pump(const Duration(milliseconds: 500));
    }
    await tester.pump(boardRefreshDelay + const Duration(seconds: 1));
    expect(fetches, 1);

    live.add(const LiveMessage('ops', {'event': 'StopArrived'}));
    live.add(const LiveMessage('ops', {'event': 'StudentBoarded'}));
    live.add(const LiveMessage('ops', {'event': 'StudentBoarded'}));
    await tester.pump();
    await tester.pump(boardRefreshDelay + const Duration(milliseconds: 100));
    await tester.pump();
    expect(fetches, 2);

    await tester.pumpWidget(const SizedBox.shrink()); // dispose the board's timers
  });

  // ---------------- Boarding errors ----------------
  test('scan errors read as plain guidance', () {
    expect(explainScanError(ApiException('x', code: 'qr_expired')).$1, 'That code has just changed');
    expect(explainScanError(ApiException('x', code: 'already_boarded')).$1, "You're already on board");
    final other = explainScanError(ApiException('Try again in a minute.', code: 'rate_limited'));
    expect(other.$2, 'Try again in a minute.');
  });
}
