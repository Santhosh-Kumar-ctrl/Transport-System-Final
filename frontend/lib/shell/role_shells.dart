import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/auth/session.dart';
import '../design/design.dart';
import '../modules/notifications/data/notifications_api.dart';
import '../modules/notifications/state/notifications_controller.dart';

class NavItem {
  const NavItem(this.label, this.path, this.icon, {this.showUnread = false});

  final String label;
  final String path;
  final IconData icon;
  final bool showUnread;
}

/// Bottom bar for phones: a white strip with a top rule; the active item carries a
/// sign-blue bar above it (like the highlighted station on a line strip).
class _BottomBar extends ConsumerWidget {
  const _BottomBar({required this.items, required this.location, this.onDark = false});

  final List<NavItem> items;
  final String location;
  final bool onDark;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final unread = ref.watch(unreadCountProvider).asData?.value ?? 0;
    final bg = onDark ? TransitColors.board : TransitColors.white;
    final active = onDark ? TransitColors.led : TransitColors.signBlue;
    final idle = onDark ? TransitColors.white.withValues(alpha: 0.6) : TransitColors.inkSoft;
    final current = items.lastIndexWhere((i) => location == i.path || location.startsWith('${i.path}/'));
    return Material(
      color: bg,
      child: SafeArea(
        top: false,
        child: Container(
          decoration: BoxDecoration(
            border: Border(top: BorderSide(color: onDark ? TransitColors.boardLine : TransitColors.rule)),
          ),
          child: Row(
            children: [
              for (var i = 0; i < items.length; i++)
                Expanded(
                  child: InkWell(
                    onTap: () => context.go(items[i].path),
                    child: Container(
                      height: 62,
                      decoration: BoxDecoration(
                        border: Border(top: BorderSide(color: i == current ? active : Colors.transparent, width: 3)),
                      ),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(items[i].icon, size: 22, color: i == current ? active : idle),
                              if (items[i].showUnread && unread > 0) ...[
                                const SizedBox(width: 4),
                                StatusPlate('$unread', tone: Tone.late),
                              ],
                            ],
                          ),
                          const SizedBox(height: 3),
                          Text(
                            items[i].label,
                            style: TransitType.small.copyWith(
                              color: i == current ? active : idle,
                              fontWeight: i == current ? FontWeight.w800 : FontWeight.w600,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class StudentShell extends StatelessWidget {
  const StudentShell({super.key, required this.child, required this.location});

  final Widget child;
  final String location;

  static const items = [
    NavItem('My line', '/student', Icons.linear_scale),
    NavItem('Alerts', '/student/alerts', Icons.notifications_none, showUnread: true),
    NavItem('Trips', '/student/trips', Icons.event_note_outlined),
    NavItem('Reports', '/student/reports', Icons.report_outlined),
  ];

  @override
  Widget build(BuildContext context) => LiveNotificationListener(
    child: Scaffold(
      body: child,
      bottomNavigationBar: _BottomBar(items: items, location: location),
    ),
  );
}

class DriverShell extends StatelessWidget {
  const DriverShell({super.key, required this.child, required this.location});

  final Widget child;
  final String location;

  static const items = [
    NavItem('Runs', '/driver', Icons.directions_bus_outlined),
    NavItem('Alerts', '/driver/alerts', Icons.notifications_none, showUnread: true),
  ];

  @override
  Widget build(BuildContext context) => LiveNotificationListener(
    child: Scaffold(
      backgroundColor: TransitColors.board,
      body: child,
      bottomNavigationBar: _BottomBar(items: items, location: location, onDark: true),
    ),
  );
}

/// Transport office: a sign-blue rail on wide screens, a drawer on phones.
class AdminShell extends ConsumerWidget {
  const AdminShell({super.key, required this.child, required this.location});

  final Widget child;
  final String location;

  static const _all = [
    NavItem('Live board', '/admin', Icons.dashboard_outlined),
    NavItem('Live map', '/admin/map', Icons.map_outlined),
    NavItem('Network', '/admin/network', Icons.alt_route),
    NavItem('Fleet', '/admin/fleet', Icons.directions_bus_outlined),
    NavItem('People', '/admin/people', Icons.badge_outlined),
    NavItem('Allocation', '/admin/allocation', Icons.group_add_outlined),
    NavItem('Schedules', '/admin/schedules', Icons.schedule),
    NavItem('Reports', '/admin/reports', Icons.fact_check_outlined),
    NavItem('Issues', '/admin/issues', Icons.report_outlined),
    NavItem('Alerts', '/admin/alerts', Icons.notifications_none, showUnread: true),
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final user = ref.watch(sessionProvider)?.user;
    final wide = MediaQuery.sizeOf(context).width >= 960;
    final rail = _Rail(items: _all, location: location, user: user);
    return LiveNotificationListener(
      child: wide
          ? Scaffold(
              body: Row(
                children: [
                  SizedBox(width: 236, child: rail),
                  Expanded(child: child),
                ],
              ),
            )
          : Scaffold(
              drawer: Drawer(width: 280, shape: const RoundedRectangleBorder(), child: rail),
              body: Builder(
                builder: (context) => Column(
                  children: [
                    Material(
                      color: TransitColors.signBlueDeep,
                      child: SafeArea(
                        bottom: false,
                        child: SizedBox(
                          height: 52,
                          child: Row(
                            children: [
                              IconButton(
                                tooltip: 'Menu',
                                icon: const Icon(Icons.menu, color: TransitColors.white),
                                onPressed: () => Scaffold.of(context).openDrawer(),
                              ),
                              Text(
                                'Transport office',
                                style: TransitType.subheading.copyWith(color: TransitColors.white),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                    Expanded(child: child),
                  ],
                ),
              ),
            ),
    );
  }
}

class _Rail extends ConsumerWidget {
  const _Rail({required this.items, required this.location, required this.user});

  final List<NavItem> items;
  final String location;
  final AppUser? user;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final unread = ref.watch(unreadCountProvider).asData?.value ?? 0;
    final current = items.lastIndexWhere((i) => location == i.path || location.startsWith('${i.path}/'));
    return Material(
      color: TransitColors.signBlueDeep,
      child: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(Space.l, Space.xl, Space.l, Space.xl),
              child: Row(
                children: [
                  const RouteBadge(code: 'T', color: TransitColors.led, size: 36),
                  const SizedBox(width: Space.m),
                  Expanded(
                    child: Text(
                      'Transport office',
                      style: TransitType.heading.copyWith(color: TransitColors.white, height: 1.1),
                    ),
                  ),
                ],
              ),
            ),
            for (var i = 0; i < items.length; i++)
              InkWell(
                onTap: () {
                  Scaffold.maybeOf(context)?.closeDrawer();
                  context.go(items[i].path);
                },
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: Space.l, vertical: 13),
                  decoration: BoxDecoration(
                    color: i == current ? TransitColors.signBlue : null,
                    border: Border(
                      left: BorderSide(color: i == current ? TransitColors.led : Colors.transparent, width: 4),
                    ),
                  ),
                  child: Row(
                    children: [
                      Icon(
                        items[i].icon,
                        size: 20,
                        color: TransitColors.white.withValues(alpha: i == current ? 1 : 0.7),
                      ),
                      const SizedBox(width: Space.m),
                      Expanded(
                        child: Text(
                          items[i].label,
                          style: TransitType.subheading.copyWith(
                            color: TransitColors.white.withValues(alpha: i == current ? 1 : 0.8),
                            fontWeight: i == current ? FontWeight.w800 : FontWeight.w600,
                          ),
                        ),
                      ),
                      if (items[i].showUnread && unread > 0) StatusPlate('$unread', tone: Tone.late),
                    ],
                  ),
                ),
              ),
            const Spacer(),
            const Divider(color: TransitColors.signBlue, height: 1),
            Padding(
              padding: const EdgeInsets.all(Space.l),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      user?.fullName ?? '',
                      style: TransitType.small.copyWith(color: TransitColors.white.withValues(alpha: 0.8)),
                    ),
                  ),
                  TextButton(
                    onPressed: () => ref.read(sessionProvider.notifier).logout(),
                    style: TextButton.styleFrom(foregroundColor: TransitColors.white),
                    child: const Text('Sign out'),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
