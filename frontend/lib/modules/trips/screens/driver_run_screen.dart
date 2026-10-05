import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/api/api_client.dart';
import '../../../core/format.dart';
import '../../../core/realtime/realtime.dart';
import '../../../design/design.dart';
import '../../boarding/data/boarding_api.dart';
import '../../dashboard/data/dashboard_api.dart';
import '../../delay_monitor/widgets/report_delay_sheet.dart';
import '../../reports/widgets/log_found_item_sheet.dart';
import '../../tracking/data/tracking_api.dart';
import '../../tracking/state/position_reporter.dart';
import '../../tracking/widgets/live_map.dart';
import '../../tracking/widgets/sharing_strip.dart';
import '../data/trip_models.dart';
import '../data/trips_api.dart';

/// The driver's running screen: the line with one big ARRIVED action on the next stop.
/// Dark board styling for glanceability in a cab.
class DriverRunScreen extends ConsumerStatefulWidget {
  const DriverRunScreen({super.key, required this.tripId});

  final int tripId;

  @override
  ConsumerState<DriverRunScreen> createState() => _DriverRunScreenState();
}

class _DriverRunScreenState extends ConsumerState<DriverRunScreen> {
  int? _busySeq;
  var _ending = false;

  void _refresh() {
    ref.invalidate(tripProvider(widget.tripId));
    ref.invalidate(rosterProvider(widget.tripId));
    ref.invalidate(driverDashboardProvider);
  }

  Future<void> _arrive(StopEvent s) async {
    setState(() => _busySeq = s.sequence);
    try {
      await ref.read(tripActionsProvider).arrive(widget.tripId, s.sequence);
      _refresh();
    } on ApiException catch (e) {
      _toast(e.message);
    } finally {
      if (mounted) setState(() => _busySeq = null);
    }
  }

  Future<void> _end(TripDetail t) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('End this trip?', style: TransitType.heading),
        content: Text(
          'Attendance is finalised when the trip ends. Students who did not board are marked missed.',
          style: TransitType.body,
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep running')),
          SignButton(
            label: 'End trip',
            kind: SignButtonKind.danger,
            height: 44,
            onPressed: () => Navigator.pop(c, true),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _ending = true);
    try {
      await ref.read(tripActionsProvider).end(t.id);
      ref.read(positionReporterProvider.notifier).stop();
      ref.invalidate(driverDashboardProvider);
      if (mounted) context.go('/driver');
    } on ApiException catch (e) {
      _toast(e.message);
      if (mounted) setState(() => _ending = false);
    }
  }

  void _toast(String m) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));

  @override
  Widget build(BuildContext context) {
    final trip = ref.watch(tripProvider(widget.tripId));
    final roster = ref.watch(rosterProvider(widget.tripId));
    listenLive(ref, (m) {
      if (m.payload['trip_id'] == widget.tripId || m.data['trip_id'] == widget.tripId) _refresh();
    }, events: {'boarding', 'StopArrived', 'TripDelayed', 'TripDelayResolved', 'TripCancelled'});

    return Scaffold(
      backgroundColor: TransitColors.board,
      body: AsyncBody(
        value: trip,
        onDark: true,
        onRetry: _refresh,
        builder: (t) {
          final next = t.nextStop;
          final r = roster.asData?.value;
          syncTripReporting(ref, runningTripId: t.running ? t.id : null, shownTripId: t.id);
          return Column(
            children: [
              SignHeader(
                color: TransitColors.board,
                leading: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    IconButton(
                      tooltip: 'Back to runs',
                      icon: const Icon(Icons.arrow_back, color: TransitColors.white),
                      onPressed: () => context.go('/driver'),
                    ),
                    RouteBadge(code: t.route.code, color: t.route.color, size: 44, rim: true),
                  ],
                ),
                title: t.isPickup ? 'Pickup' : 'Drop',
                subtitle: t.headline,
                trailing: NumberPlate(t.busRegistration, dense: true),
              ),
              _StatusStrip(trip: t, boarded: r?.boarded, capacity: r?.capacity ?? t.busCapacity),
              if (t.running) const LocationSharingStrip(),
              if (t.running)
                Padding(
                  padding: const EdgeInsets.fromLTRB(Space.gutter, Space.m, Space.gutter, 0),
                  child: Column(
                    children: [
                      SignButton(
                        label: 'Show boarding QR',
                        icon: Icons.qr_code_2,
                        height: 60,
                        expand: true,
                        onPressed: () => context.push('/driver/trip/${t.id}/qr'),
                      ),
                      const SizedBox(height: Space.s),
                      Row(
                        children: [
                          Expanded(
                            child: SignButton(
                              label: 'Riders',
                              icon: Icons.people_outline,
                              kind: SignButtonKind.onDark,
                              height: 50,
                              onPressed: () => context.push('/driver/trip/${t.id}/roster'),
                            ),
                          ),
                          const SizedBox(width: Space.s),
                          Expanded(
                            child: SignButton(
                              label: 'Running late',
                              icon: Icons.schedule,
                              kind: SignButtonKind.onDark,
                              height: 50,
                              onPressed: () => showReportDelaySheet(context, tripId: t.id),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              if (t.running || t.status == TripStatus.completed)
                Padding(
                  padding: const EdgeInsets.fromLTRB(Space.gutter, Space.s, Space.gutter, 0),
                  child: SignButton(
                    label: 'Found item',
                    icon: Icons.backpack_outlined,
                    kind: SignButtonKind.onDark,
                    height: 50,
                    expand: true,
                    onPressed: () => showLogFoundItemSheet(context, tripId: t.id),
                  ),
                ),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(0, Space.l, Space.gutter, Space.xl),
                  children: [
                    if (t.running) _DriverMap(tripId: t.id),
                    LineDiagram(
                      color: t.route.color,
                      onDark: true,
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
                      trailingFor: (i, _) {
                        final s = t.stops[i];
                        if (!t.running || next == null || s.sequence != next.sequence) return null;
                        return SignButton(
                          label: 'Arrived',
                          kind: SignButtonKind.go,
                          height: 48,
                          busy: _busySeq == s.sequence,
                          onPressed: () => _arrive(s),
                        );
                      },
                    ),
                    if (t.running)
                      Padding(
                        padding: const EdgeInsets.fromLTRB(Space.gutter, Space.xl, 0, 0),
                        child: SignButton(
                          label: next == null || next.sequence == t.stops.last.sequence
                              ? 'End trip at ${t.stops.last.stopName}'
                              : 'End trip early',
                          kind: next == null ? SignButtonKind.danger : SignButtonKind.onDark,
                          expand: true,
                          height: 56,
                          busy: _ending,
                          onPressed: () => _end(t),
                        ),
                      ),
                  ],
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _StatusStrip extends StatelessWidget {
  const _StatusStrip({required this.trip, required this.boarded, required this.capacity});

  final TripDetail trip;
  final int? boarded;
  final int capacity;

  @override
  Widget build(BuildContext context) {
    final t = trip;
    final late = t.currentDelayMin >= 1;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: Space.gutter, vertical: Space.m),
      decoration: const BoxDecoration(
        border: Border(
          top: BorderSide(color: TransitColors.boardLine),
          bottom: BorderSide(color: TransitColors.boardLine),
        ),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  t.running ? 'Schedule' : 'Status',
                  style: TransitType.small.copyWith(color: TransitColors.white.withValues(alpha: 0.6)),
                ),
                Text(
                  switch (t.status) {
                    TripStatus.inProgress => delayLabel(t.currentDelayMin),
                    TripStatus.scheduled => 'Starts ${hm(t.scheduledDeparture)}',
                    TripStatus.completed => 'Finished',
                    TripStatus.cancelled => 'Cancelled',
                  },
                  style: TransitType.heading.copyWith(
                    color: late && t.running ? TransitColors.led : TransitColors.white,
                  ),
                ),
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text('On board', style: TransitType.small.copyWith(color: TransitColors.white.withValues(alpha: 0.6))),
              Text(
                boarded == null ? '–' : '$boarded / $capacity',
                style: TransitType.heading.copyWith(color: TransitColors.white),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// The driver's own bus on the map, from the phone's latest fix (fresher than the server's copy).
class _DriverMap extends ConsumerWidget {
  const _DriverMap({required this.tripId});

  final int tripId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final live = ref.watch(liveTripProvider(tripId)).asData?.value;
    final fix = ref.watch(positionReporterProvider.select((s) => s.lastFix));
    if (live == null) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.fromLTRB(Space.gutter, 0, 0, Space.l),
      child: LiveMap(trips: [live], localFix: fix, followTripId: tripId, height: 220),
    );
  }
}
