/// Composition root for the Flutter app: the ONLY file that knows every module's
/// screens. Mirrors backend/app/main.py.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'core/auth/session.dart';
import 'design/design.dart';
import 'modules/allocation/screens/admin_allocation_screen.dart';
import 'modules/auth/screens/admin_people_screen.dart';
import 'modules/auth/screens/login_screen.dart';
import 'modules/boarding/screens/driver_qr_screen.dart';
import 'modules/boarding/screens/driver_roster_screen.dart';
import 'modules/boarding/screens/student_attendance_screen.dart';
import 'modules/boarding/screens/student_scan_screen.dart';
import 'modules/dashboard/screens/admin_board_screen.dart';
import 'modules/dashboard/screens/student_home_screen.dart';
import 'modules/history/screens/admin_history_screen.dart';
import 'modules/master_data/screens/admin_fleet_screen.dart';
import 'modules/master_data/screens/admin_network_screen.dart';
import 'modules/master_data/screens/route_editor_screen.dart';
import 'modules/notifications/screens/inbox_screen.dart';
import 'modules/reports/screens/admin_issue_screen.dart';
import 'modules/reports/screens/admin_issues_screen.dart';
import 'modules/reports/screens/my_reports_screen.dart';
import 'modules/reports/screens/report_form_screen.dart';
import 'modules/tracking/screens/admin_live_map_screen.dart';
import 'modules/trips/screens/admin_schedules_screen.dart';
import 'modules/trips/screens/driver_home_screen.dart';
import 'modules/trips/screens/driver_run_screen.dart';
import 'shell/role_shells.dart';

String homeFor(Role role) => switch (role) {
  Role.student => '/student',
  Role.driver => '/driver',
  Role.admin => '/admin',
};

final routerProvider = Provider<GoRouter>((ref) {
  final refresh = ValueNotifier(0);
  ref.listen(sessionProvider, (_, _) => refresh.value++);
  ref.onDispose(refresh.dispose);

  int id(GoRouterState s) => int.parse(s.pathParameters['id']!);

  return GoRouter(
    initialLocation: '/',
    refreshListenable: refresh,
    redirect: (context, state) {
      final session = ref.read(sessionProvider);
      final loc = state.matchedLocation;
      if (session == null) return loc == '/login' ? null : '/login';
      final home = homeFor(session.user.role);
      // Role guard: every role stays inside its own area.
      if (loc == '/' || loc == '/login' || !(loc == home || loc.startsWith('$home/'))) return home;
      return null;
    },
    routes: [
      GoRoute(path: '/', builder: (_, _) => const SizedBox.shrink()),
      GoRoute(path: '/login', builder: (_, _) => const LoginScreen()),

      // ---- Student ----
      ShellRoute(
        builder: (_, state, child) => StudentShell(location: state.matchedLocation, child: child),
        routes: [
          GoRoute(path: '/student', builder: (_, _) => const StudentHomeScreen()),
          GoRoute(path: '/student/alerts', builder: (_, _) => const InboxScreen()),
          GoRoute(path: '/student/trips', builder: (_, _) => const StudentAttendanceScreen()),
          GoRoute(path: '/student/reports', builder: (_, _) => const MyReportsScreen()),
          // Inside the shell, like the tabs: a page stacked over the shell made Flutter web throw
          // a focus-traversal error when the form navigated back into it after sending.
          GoRoute(
            path: '/student/reports/new',
            builder: (_, s) => ReportFormScreen(tripId: int.tryParse(s.uri.queryParameters['trip'] ?? '')),
          ),
          GoRoute(
            path: '/student/reports/:id',
            builder: (_, s) => StudentReportScreen(reportId: id(s)),
          ),
        ],
      ),
      GoRoute(path: '/student/scan', builder: (_, _) => const StudentScanScreen()),

      // ---- Driver ----
      ShellRoute(
        builder: (_, state, child) => DriverShell(location: state.matchedLocation, child: child),
        routes: [
          GoRoute(path: '/driver', builder: (_, _) => const DriverHomeScreen()),
          GoRoute(path: '/driver/alerts', builder: (_, _) => const InboxScreen(onDark: true)),
        ],
      ),
      GoRoute(
        path: '/driver/trip/:id',
        builder: (_, s) => DriverRunScreen(tripId: id(s)),
      ),
      GoRoute(
        path: '/driver/trip/:id/qr',
        builder: (_, s) => DriverQrScreen(tripId: id(s)),
      ),
      GoRoute(
        path: '/driver/trip/:id/roster',
        builder: (_, s) => DriverRosterScreen(tripId: id(s)),
      ),

      // ---- Transport office (admin) ----
      ShellRoute(
        builder: (_, state, child) => AdminShell(location: state.matchedLocation, child: child),
        routes: [
          GoRoute(path: '/admin', builder: (_, _) => const AdminBoardScreen()),
          GoRoute(path: '/admin/map', builder: (_, _) => const AdminLiveMapScreen()),
          GoRoute(path: '/admin/network', builder: (_, _) => const AdminNetworkScreen()),
          GoRoute(
            path: '/admin/network/:id',
            builder: (_, s) => RouteEditorScreen(routeId: id(s)),
          ),
          GoRoute(path: '/admin/fleet', builder: (_, _) => const AdminFleetScreen()),
          GoRoute(path: '/admin/people', builder: (_, _) => const AdminPeopleScreen()),
          GoRoute(path: '/admin/allocation', builder: (_, _) => const AdminAllocationScreen()),
          GoRoute(path: '/admin/schedules', builder: (_, _) => const AdminSchedulesScreen()),
          GoRoute(path: '/admin/reports', builder: (_, _) => const AdminHistoryScreen()),
          GoRoute(path: '/admin/issues', builder: (_, _) => const AdminIssuesScreen()),
          GoRoute(
            path: '/admin/issues/:id',
            builder: (_, s) => AdminIssueScreen(reportId: id(s)),
          ),
          GoRoute(path: '/admin/alerts', builder: (_, _) => const InboxScreen()),
        ],
      ),
    ],
  );
});

class TransitApp extends ConsumerWidget {
  const TransitApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp.router(
      title: 'Transit',
      debugShowCheckedModeBanner: false,
      theme: buildTransitTheme(),
      routerConfig: ref.watch(routerProvider),
    );
  }
}
