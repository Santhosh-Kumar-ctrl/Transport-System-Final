import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/format.dart';
import '../../../core/realtime/realtime.dart';
import '../../../design/design.dart';
import '../../capacity/widgets/utilization_list.dart';
import '../../history/data/history_api.dart';
import '../../trips/data/trip_models.dart';
import '../../trips/data/trips_api.dart';
import '../data/dashboard_api.dart';

const _delayThreshold = 5;

/// Live messages that change the board. GPS fixes (`position`, every few seconds from every bus)
/// don't: the live map follows those.
bool refreshesBoard(LiveMessage m) => m.type != 'position';

/// Several updates in a row (a bus arriving, then boardings) cause one refetch, this long after the first.
const boardRefreshDelay = Duration(seconds: 2);

/// The transport office's live departure board.
class AdminBoardScreen extends ConsumerStatefulWidget {
  const AdminBoardScreen({super.key});

  @override
  ConsumerState<AdminBoardScreen> createState() => _AdminBoardScreenState();
}

class _AdminBoardScreenState extends ConsumerState<AdminBoardScreen> {
  late final Timer _clock;
  Timer? _refetch;

  void _scheduleRefresh() {
    if (_refetch?.isActive ?? false) return;
    _refetch = Timer(boardRefreshDelay, () {
      if (mounted) ref.invalidate(adminDashboardProvider);
    });
  }

  @override
  void initState() {
    super.initState();
    // Re-render every 20s: clock, "not started" states and relative times move on.
    _clock = Timer.periodic(const Duration(seconds: 20), (_) => setState(() {}));
  }

  @override
  void dispose() {
    _clock.cancel();
    _refetch?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final dash = ref.watch(adminDashboardProvider);
    listenLive(ref, (m) {
      if (refreshesBoard(m)) _scheduleRefresh();
    });

    return ColoredBox(
      color: TransitColors.enamel,
      child: AsyncBody(
        value: dash,
        onRetry: () => ref.invalidate(adminDashboardProvider),
        builder: (d) => LayoutBuilder(
          builder: (context, c) {
            final wide = c.maxWidth >= 1180;
            final board = _Board(dash: d);
            final side = _SidePanel(dash: d);
            if (wide) {
              return Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(
                    child: ColoredBox(
                      color: TransitColors.board,
                      child: SingleChildScrollView(child: board),
                    ),
                  ),
                  SizedBox(width: 360, child: side),
                ],
              );
            }
            return ListView(children: [board, side]);
          },
        ),
      ),
    );
  }
}

class _Board extends ConsumerWidget {
  const _Board({required this.dash});

  final AdminDashboard dash;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final now = DateTime.now();
    final c = dash.counts;
    return Container(
      color: TransitColors.board,
      constraints: const BoxConstraints(minHeight: 400),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(Space.l, Space.xl, Space.l, Space.m),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Departures', style: TransitType.title.copyWith(color: TransitColors.white)),
                      Text(
                        dayLabel(dash.serviceDate),
                        style: TransitType.body.copyWith(color: TransitColors.white.withValues(alpha: 0.6)),
                      ),
                    ],
                  ),
                ),
                Text(hm(now), style: TransitType.display.copyWith(color: TransitColors.led, fontSize: 40)),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: Space.l),
            child: Wrap(
              spacing: Space.xl,
              runSpacing: Space.s,
              children: [
                _Count('Running', c['in_progress'] ?? 0, TransitColors.white),
                _Count(
                  'Delayed',
                  c['delayed_now'] ?? 0,
                  (c['delayed_now'] ?? 0) > 0 ? TransitColors.led : TransitColors.white,
                ),
                _Count('Upcoming', c['scheduled'] ?? 0, TransitColors.white),
                _Count('Finished', c['completed'] ?? 0, TransitColors.white.withValues(alpha: 0.6)),
                if ((c['cancelled'] ?? 0) > 0) _Count('Cancelled', c['cancelled']!, TransitColors.late),
              ],
            ),
          ),
          const SizedBox(height: Space.l),
          const Divider(color: TransitColors.boardLine, height: 1),
          if (dash.board.isEmpty)
            Padding(
              padding: const EdgeInsets.all(Space.l),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('No trips on the board today', style: TransitType.heading.copyWith(color: TransitColors.white)),
                  const SizedBox(height: Space.xs),
                  Text(
                    "Trips are created from schedules automatically. If you've just added schedules, create today's trips now.",
                    style: TransitType.body.copyWith(color: TransitColors.white.withValues(alpha: 0.7)),
                  ),
                  const SizedBox(height: Space.l),
                  SignButton(
                    label: "Create today's trips",
                    kind: SignButtonKind.onDark,
                    onPressed: () async {
                      await ref.read(tripActionsProvider).generateToday();
                      ref.invalidate(adminDashboardProvider);
                    },
                  ),
                ],
              ),
            ),
          for (final r in dash.board) _row(context, r, now),
        ],
      ),
    );
  }

  Widget _row(BuildContext context, BoardRow r, DateTime now) {
    final notStarted =
        r.status == TripStatus.scheduled &&
        now.isAfter(r.scheduledDeparture.add(const Duration(minutes: _delayThreshold)));
    final (status, color) = switch (r.status) {
      TripStatus.inProgress when r.delayMin >= _delayThreshold => (
        'Delayed ${delayLabel(r.delayMin)}',
        TransitColors.led,
      ),
      TripStatus.inProgress => (r.delayMin >= 1 ? delayLabel(r.delayMin) : 'On time', const Color(0xFF6FD39A)),
      TripStatus.scheduled when notStarted => ('Not started', TransitColors.led),
      TripStatus.scheduled => ('Scheduled', TransitColors.white),
      TripStatus.completed => ('Arrived', TransitColors.white.withValues(alpha: 0.5)),
      TripStatus.cancelled => ('Cancelled', const Color(0xFFFF7A70)),
    };
    final dirWord = r.direction == 'pickup' ? 'Pickup' : 'Drop';
    final detail = switch (r.status) {
      TripStatus.inProgress when r.nextStopName != null =>
        '$dirWord, next ${r.nextStopName} at ${hm(r.nextStopExpected!)}, stop ${r.stopsDone + 1} of ${r.stopsTotal}, ${r.driverName}',
      TripStatus.completed => '$dirWord, ${r.boarded} of ${r.allocated} rode, ${r.driverName}',
      _ => '$dirWord, ${r.allocated} allocated, ${r.driverName}',
    };
    return DepartureRow(
      routeCode: r.route.code,
      routeColor: r.route.color,
      headline: r.route.name,
      detail: detail,
      registration: r.registration,
      scheduled: r.scheduledDeparture,
      expected: r.status == TripStatus.inProgress || notStarted
          ? r.scheduledDeparture.add(
              Duration(minutes: notStarted ? now.difference(r.scheduledDeparture).inMinutes : r.delayMin),
            )
          : null,
      status: status,
      statusColor: color,
      taken: r.boarded,
      capacity: r.capacity,
      dimmed: r.status == TripStatus.completed || r.status == TripStatus.cancelled,
      onTap: () => showDialog(
        context: context,
        builder: (_) => _TripDialog(tripId: r.tripId),
      ),
    );
  }
}

class _Count extends StatelessWidget {
  const _Count(this.label, this.value, this.color);

  final String label;
  final int value;
  final Color color;

  @override
  Widget build(BuildContext context) => Row(
    mainAxisSize: MainAxisSize.min,
    crossAxisAlignment: CrossAxisAlignment.end,
    children: [
      Text(
        '$value',
        style: TransitType.title.copyWith(color: color, fontFeatures: const [FontFeature.tabularFigures()]),
      ),
      const SizedBox(width: 6),
      Padding(
        padding: const EdgeInsets.only(bottom: 3),
        child: Text(label, style: TransitType.body.copyWith(color: TransitColors.white.withValues(alpha: 0.7))),
      ),
    ],
  );
}

class _SidePanel extends StatelessWidget {
  const _SidePanel({required this.dash});

  final AdminDashboard dash;

  @override
  Widget build(BuildContext context) {
    final routes = {for (final u in dash.utilization) u.route.id: u.route};
    return ListView(
      shrinkWrap: true,
      physics: const ClampingScrollPhysics(),
      padding: const EdgeInsets.all(Space.l),
      children: [
        const Text("Today's alerts", style: TransitType.heading),
        const SizedBox(height: Space.s),
        if (dash.alerts.isEmpty)
          Text('Nothing needs attention right now.', style: TransitType.body.copyWith(color: TransitColors.inkSoft)),
        for (final a in dash.alerts) _AlertLine(alert: a, routes: routes),
        const SizedBox(height: Space.xl),
        const Text('Seats allocated per route', style: TransitType.heading),
        const SizedBox(height: Space.s),
        UtilizationList(items: dash.utilization),
      ],
    );
  }
}

/// One-line operational summary of an event, for the alerts column.
String describeEvent(AlertEvent a, Map<int, RouteRef> routes) {
  final p = a.payload;
  final code = routes[p['route_id']]?.code ?? '';
  final r = code.isEmpty ? 'A bus' : 'Route $code';
  return switch (a.type) {
    'TripDelayed' =>
      '$r is ${p['delay_min']} min late${switch (p['source']) {
        'stop_arrival' => ' at ${p['at_stop_name']}',
        'overdue' => ', no check-in at ${p['at_stop_name']}',
        'not_started' => ', not started',
        'manual' => ': ${p['reason']}',
        _ => '',
      }}',
    'TripDelayResolved' => '$r is back on schedule',
    'OverCapacity' => '$r over capacity, ${p['boarded']} on ${p['capacity']} seats',
    'CapacityWarning' => '$r nearly full, ${p['boarded']} of ${p['capacity']}',
    'UnallocatedBoarding' => '${p['student_name']} boarded route $code without an allocation for it',
    'TripCancelled' => '$r trip cancelled: ${p['reason']}',
    'TripStarted' => '$r started${(p['delay_min'] as int? ?? 0) >= 1 ? ' ${p['delay_min']} min late' : ''}',
    'StopArrived' =>
      '$r reached ${p['stop_name']}${(p['delay_min'] as int? ?? 0) >= 1 ? ', ${p['delay_min']} min late' : ''}',
    'StudentBoarded' => '${p['student_name']} boarded',
    'TripEnded' => '$r finished',
    'AttendanceFinalized' => 'Attendance: ${p['present']} rode, ${p['absent']} missed',
    _ => a.type,
  };
}

class _AlertLine extends StatelessWidget {
  const _AlertLine({required this.alert, required this.routes});

  final AlertEvent alert;
  final Map<int, RouteRef> routes;

  @override
  Widget build(BuildContext context) {
    final edge = switch (alert.type) {
      'OverCapacity' || 'TripCancelled' => TransitColors.late,
      'TripDelayed' => (alert.payload['delay_min'] as int? ?? 0) >= 15 ? TransitColors.late : TransitColors.caution,
      'TripDelayResolved' => TransitColors.go,
      _ => TransitColors.caution,
    };
    return Container(
      margin: const EdgeInsets.only(bottom: Space.s),
      padding: const EdgeInsets.all(Space.m),
      decoration: BoxDecoration(
        color: TransitColors.white,
        borderRadius: Radii.signAll,
        border: Border(left: BorderSide(color: edge, width: 5)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(child: Text(describeEvent(alert, routes), style: TransitType.body)),
          const SizedBox(width: Space.s),
          Text(hm(alert.occurredAt), style: TransitType.small.copyWith(color: TransitColors.inkSoft)),
        ],
      ),
    );
  }
}

/// Trip detail for the office: the live line plus everything that happened on the trip.
class _TripDialog extends ConsumerWidget {
  const _TripDialog({required this.tripId});

  final int tripId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final trip = ref.watch(tripProvider(tripId));
    final timeline = ref.watch(timelineProvider(tripId));
    return Dialog(
      insetPadding: const EdgeInsets.all(Space.gutter),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 760, maxHeight: 720),
        child: AsyncBody(
          value: trip,
          builder: (t) => Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              SignHeader(
                leading: RouteBadge(code: t.route.code, color: t.route.color, rim: true),
                title: '${t.route.name}, ${t.isPickup ? 'pickup' : 'drop'}',
                subtitle: '${t.driverName}, departs ${hm(t.scheduledDeparture)}',
                trailing: NumberPlate(t.busRegistration),
              ),
              Flexible(
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(0, Space.m, Space.l, Space.l),
                  children: [
                    LineDiagram(
                      color: t.route.color,
                      showBus: t.running,
                      stops: [
                        for (final s in t.stops)
                          LineStop(
                            name: s.stopName,
                            scheduled: s.scheduledAt,
                            arrived: s.arrivedAt,
                            expected: s.scheduledAt.add(Duration(minutes: t.currentDelayMin)),
                            isTerminus: s == t.stops.last,
                          ),
                      ],
                    ),
                    Padding(
                      padding: const EdgeInsets.only(left: Space.l, top: Space.xl),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('What happened', style: TransitType.heading),
                          const SizedBox(height: Space.s),
                          AsyncBody(
                            value: timeline,
                            builder: (events) => events.isEmpty
                                ? Text(
                                    'Nothing yet. The trip has not started.',
                                    style: TransitType.body.copyWith(color: TransitColors.inkSoft),
                                  )
                                : Column(
                                    children: [
                                      for (final e in events)
                                        Padding(
                                          padding: const EdgeInsets.symmetric(vertical: 4),
                                          child: Row(
                                            crossAxisAlignment: CrossAxisAlignment.start,
                                            children: [
                                              SizedBox(
                                                width: 56,
                                                child: Text(
                                                  hm(e.occurredAt),
                                                  style: TransitType.figure.copyWith(fontSize: 14),
                                                ),
                                              ),
                                              Expanded(
                                                child: Text(
                                                  describeEvent(e, {t.route.id: t.route}),
                                                  style: TransitType.body,
                                                ),
                                              ),
                                            ],
                                          ),
                                        ),
                                    ],
                                  ),
                          ),
                          if (t.status == TripStatus.scheduled || t.running) ...[
                            const SizedBox(height: Space.xl),
                            SignButton(
                              label: 'Cancel this trip',
                              kind: SignButtonKind.quiet,
                              onPressed: () => _cancel(context, ref, t),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _cancel(BuildContext context, WidgetRef ref, TripDetail t) async {
    final reason = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Cancel trip', style: TransitType.heading),
        content: TextField(
          controller: reason,
          decoration: const InputDecoration(labelText: 'Reason riders will see'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep trip')),
          SignButton(
            label: 'Cancel trip',
            kind: SignButtonKind.danger,
            height: 44,
            onPressed: () => Navigator.pop(c, true),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await ref
          .read(tripActionsProvider)
          .cancel(t.id, reason.text.trim().isEmpty ? 'Cancelled by transport office' : reason.text.trim());
      ref.invalidate(tripProvider(t.id));
      ref.invalidate(adminDashboardProvider);
    } on ApiException catch (e) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    }
  }
}
