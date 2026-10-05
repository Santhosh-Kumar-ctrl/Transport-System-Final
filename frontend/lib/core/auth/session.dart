import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/api_client.dart';
import '../config.dart';

enum Role { student, driver, admin }

class AppUser {
  const AppUser({
    required this.id,
    required this.email,
    required this.fullName,
    required this.role,
    this.phone,
    this.rollNo,
  });

  final int id;
  final String email;
  final String fullName;
  final Role role;
  final String? phone;
  final String? rollNo;

  String get firstName => fullName.split(' ').first;

  factory AppUser.fromJson(Map<String, dynamic> j) => AppUser(
    id: j['id'] as int,
    email: j['email'] as String,
    fullName: j['full_name'] as String,
    role: Role.values.byName(j['role'] as String),
    phone: j['phone'] as String?,
    rollNo: (j['student'] as Map<String, dynamic>?)?['roll_no'] as String?,
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'email': email,
    'full_name': fullName,
    'role': role.name,
    'phone': phone,
    'student': rollNo == null ? null : {'roll_no': rollNo},
  };
}

class Session {
  const Session({required this.accessToken, required this.refreshToken, required this.user});

  final String accessToken;
  final String refreshToken;
  final AppUser user;

  factory Session.fromTokenPair(Map<String, dynamic> j) => Session(
    accessToken: j['access_token'] as String,
    refreshToken: j['refresh_token'] as String,
    user: AppUser.fromJson(j['user'] as Map<String, dynamic>),
  );

  Map<String, dynamic> toJson() => {'access_token': accessToken, 'refresh_token': refreshToken, 'user': user.toJson()};
}

const _storeKey = 'transit.session';

/// Where the signed-in session is kept between launches.
abstract interface class SessionStore {
  Future<String?> read();
  Future<void> write(String? value); // null clears it
}

/// Web: browser storage (localStorage). There is no safer place a web app can keep a token.
class PrefsSessionStore implements SessionStore {
  const PrefsSessionStore();

  @override
  Future<String?> read() async => (await SharedPreferences.getInstance()).getString(_storeKey);

  @override
  Future<void> write(String? value) async {
    final prefs = await SharedPreferences.getInstance();
    value == null ? await prefs.remove(_storeKey) : await prefs.setString(_storeKey, value);
  }
}

/// Phones: the platform's encrypted keystore. A session saved by an older version in plain
/// preferences is moved over on first read.
class SecureSessionStore implements SessionStore {
  const SecureSessionStore();

  static const _secure = FlutterSecureStorage();

  @override
  Future<String?> read() async {
    final value = await _secure.read(key: _storeKey);
    if (value != null) return value;
    final legacy = await const PrefsSessionStore().read();
    if (legacy != null) {
      await _secure.write(key: _storeKey, value: legacy);
      await const PrefsSessionStore().write(null);
    }
    return legacy;
  }

  @override
  Future<void> write(String? value) async =>
      value == null ? _secure.delete(key: _storeKey) : _secure.write(key: _storeKey, value: value);
}

/// Holds the signed-in session and persists it ([SessionController.store]).
class SessionController extends Notifier<Session?> {
  /// Set by main() from storage before the first frame.
  static Session? restored;

  /// Encrypted storage on phones, browser storage on web. Tests swap in a memory store.
  static SessionStore store = kIsWeb ? const PrefsSessionStore() : const SecureSessionStore();

  /// The HTTP client for login and refresh (no auth interceptor). Tests swap in a fake.
  static Dio Function() authClient = () =>
      Dio(BaseOptions(baseUrl: AppConfig.apiBase, connectTimeout: const Duration(seconds: 8)));

  late final Dio _bare = authClient();

  @override
  Session? build() => restored;

  static Future<void> restore() async {
    try {
      final raw = await store.read();
      if (raw != null) restored = Session.fromTokenPair(jsonDecode(raw) as Map<String, dynamic>);
    } catch (_) {
      restored = null; // corrupted or old format: sign in again
    }
  }

  Future<void> _save(Session? s) => store.write(s == null ? null : jsonEncode(s.toJson()));

  Future<void> login(String email, String password) async {
    try {
      final r = await _bare.post('/auth/login', data: {'email': email.trim(), 'password': password});
      final s = Session.fromTokenPair(r.data as Map<String, dynamic>);
      await _save(s);
      state = s;
    } on DioException catch (e) {
      throw ApiException.fromDio(e);
    }
  }

  /// Returns the new access token, or null if the session can't be renewed right now.
  ///
  /// Only a 401 from the server (session expired, revoked or account deactivated) signs the user
  /// out. A timeout or a server error keeps the session: a driver on patchy mobile data stays
  /// signed in (and keeps sharing the bus's location) and the next call simply tries again.
  Future<String?> refresh() async {
    final current = state;
    if (current == null) return null;
    try {
      final r = await _bare.post('/auth/refresh', data: {'refresh_token': current.refreshToken});
      final s = Session.fromTokenPair(r.data as Map<String, dynamic>);
      await _save(s);
      state = s;
      return s.accessToken;
    } on DioException catch (e) {
      if (e.response?.statusCode == 401) await logout();
      return null;
    }
  }

  Future<void> logout() async {
    await _save(null);
    state = null;
  }
}

final sessionProvider = NotifierProvider<SessionController, Session?>(SessionController.new);
