import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/auth/session.dart';
import '../../../core/format.dart';
import '../../../core/realtime/realtime.dart';
import '../../../design/design.dart';
import '../../master_data/data/master_data_api.dart';
import '../../tracking/data/tracking_api.dart';
import '../../tracking/widgets/live_map.dart';
import '../../trips/data/trip_models.dart';
import '../../trips/data/trips_api.dart';
import '../data/dashboard_api.dart';

const _liveEvents = {
  'TripStarted',
  'StopArrived',
  'TripDelayed',
  'TripDelayResolved',
  'TripEnded',
  'TripCancelled',
  'StudentBoarded',
};

class StudentHomeScreen extends ConsumerWidget {
  const StudentHomeScreen({super.key});

  /// The trip that matters right now: running > next upcoming > last of the day.
  static StudentTrip? focusTrip(List<StudentTrip> trips) {
    if (trips.isEmpty) return null;
    final running = trips.where((t) => t.status == TripStatus.inProgress);
    if (running.isNotEmpty) return running.first;
    final soon = DateTime.now().subtract(const Duration(minutes: 30));
    final upcoming = trips.where((t) => t.status == TripStatus.scheduled && t.scheduledDeparture.isAfter(soon));
    return upcoming.isNotEmpty ? upcoming.first : trips.last;
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final dash = ref.watch(studentDashboardProvider);
    listenLive(ref, (_) {
      ref.invalidate(studentDashboardProvider);
      ref.invalidate(tripProvider);
    }, events: _liveEvents);

    // Follow my route's live channel once we know it.
    final routeId = dash.asData?.value.route?.id;
    if (routeId != null) ref.read(realtimeProvider)?.subscribe('route:$routeId');

    return AsyncBody(
      value: dash,
      onRetry: () => ref.invalidate(studentDashboardProvider),
      builder: (d) => RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(tripProvider);
          final _ = await ref.refresh(studentDashboardProvider.future);
        },
        child: d.route == null ? _NotAllocated(ref: ref) : _MyLine(dash: d),
      ),
    );
  }
}

class _NotAllocated extends StatelessWidget {
  const _NotAllocated({required this.ref});

  final WidgetRef ref;

  @override
  Widget build(BuildContext context) {
    final name = ref.watch(sessionProvider)?.user.firstName ?? '';
    return ListView(
      children: [
        SignHeader(
          title: 'Hello $name',
          subtitle: 'Your bus pass',
          trailing: _SignOut(ref: ref),
        ),
        const SignNotice(
          title: "You're not on a route yet",
          body:
              'The transport office assigns routes and stops. As soon as you are assigned, '
              'your bus, your stop and live timings appear here.',
        ),
      ],
    );
  }
}

class _SignOut extends StatelessWidget {
  const _SignOut({required this.ref});

  final WidgetRef ref;

  @override
  Widget build(BuildContext context) => IconButton(
    tooltip: 'Sign out',
    icon: const Icon(Icons.logout, color: TransitColors.white),
    onPressed: () => ref.read(sessionProvider.notifier).logout(),
  );
}

class _MyLine extends ConsumerWidget {
  const _MyLine({required this.dash});

  final StudentDashboard dash;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final route = dash.route!;
    final myStop = route.stops.firstWhere((s) => s.stop.id == dash.myStopId, orElse: () => route.stops.first);
    final focus = StudentHomeScreen.focusTrip(dash.trips);
    final others = dash.trips.where((t) => t.tripId != focus?.tripId).toList();

    return ListView(
      padding: EdgeInsets.zero,
      children: [
        SignHeader(
          leading: RouteBadge(code: route.code, color: route.color, size: 52, rim: true),
          title: route.name,
          subtitle: 'Your stop: ${myStop.stop.name}',
          trailing: _SignOut(ref: ref),
        ),
        Container(height: 8, color: route.color),
        if (focus == null)
          const SignNotice(title: 'No buses today', body: 'There is no service on your route today.')
        else ...[
          _NextBusPanel(trip: focus, stopName: myStop.stop.name),
          if (focus.status == TripStatus.inProgress && !focus.boarded)
            Padding(
              padding: const EdgeInsets.fromLTRB(Space.gutter, 0, Space.gutter, Space.l),
              child: SignButton(
                label: 'Scan to board',
                icon: Icons.qr_code_scanner,
                expand: true,
                height: 64,
                onPressed: () => context.push('/student/scan'),
              ),
            ),
          if (focus.status == TripStatus.inProgress) _BusMap(tripId: focus.tripId, myStopId: myStop.stop.id),
          _LiveLine(tripId: focus.tripId, route: route, myStopId: myStop.stop.id, delayMin: focus.delayMin),
        ],
        if (others.isNotEmpty) ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(Space.gutter, Space.xl, Space.gutter, Space.s),
            child: Text('Other buses today', style: TransitType.heading),
          ),
          for (final t in others) _OtherTripRow(trip: t),
        ],
        Padding(
          padding: const EdgeInsets.fromLTRB(Space.gutter, Space.xl, Space.gutter, 0),
          child: SignButton(
            label: 'Report a problem',
            icon: Icons.report_outlined,
            kind: SignButtonKind.quiet,
            expand: true,
            onPressed: () => context.go('/student/reports/new'),
          ),
        ),
        const SizedBox(height: Space.xxl),
      ],
    );
  }
}

/// The platform indicator: when the bus reaches *my* stop.
class _NextBusPanel extends StatelessWidget {
  const _NextBusPanel({required this.trip, required this.stopName});

  final StudentTrip trip;
  final String stopName;

  @override
  Widget build(BuildContext context) {
    final t = trip;
    final label = t.isPickup ? 'Morning pickup' : 'Evening drop';
    final (plate, tone) = switch (t.status) {
      TripStatus.cancelled => ('Cancelled', Tone.late),
      TripStatus.completed => ('Finished', Tone.neutral),
      _ when t.boarded => ('Boarded ${hm(t.boardedAt!)}', Tone.go),
      _ when t.myStopArrived != null => ('Bus has left your stop', Tone.neutral),
      _ when t.delayMin >= 1 => (delayLabel(t.delayMin), Tone.late),
      TripStatus.inProgress => ('On time', Tone.go),
      _ => ('Not started', Tone.neutral),
    };
    final big = t.myStopArrived ?? t.myStopExpected ?? t.scheduledDeparture;
    final late = t.delayMin >= 1 && t.myStopArrived == null && t.status != TripStatus.completed;

    return Container(
      margin: const EdgeInsets.all(Space.gutter),
      padding: const EdgeInsets.all(Space.l),
      decoration: const BoxDecoration(color: TransitColors.white, borderRadius: Radii.signAll),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(label, style: TransitType.subheading.copyWith(color: TransitColors.inkSoft)),
              ),
              NumberPlate(t.registration),
            ],
          ),
          const SizedBox(height: Space.m),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                hm(big),
                style: TransitType.display.copyWith(color: late ? TransitColors.late : TransitColors.ink, fontSize: 56),
              ),
              const SizedBox(width: Space.m),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Text(
                    t.myStopArrived != null ? 'reached $stopName' : 'at $stopName',
                    style: TransitType.subheading,
                  ),
                ),
              ),
            ],
          ),
          if (late && t.myStopScheduled != null)
            Text(
              'Timetable ${hm(t.myStopScheduled!)}',
              style: TransitType.small.copyWith(color: TransitColors.inkSoft),
            ),
          const SizedBox(height: Space.m),
          Row(
            children: [
              StatusPlate(plate, tone: tone, large: true),
              const SizedBox(width: Space.m),
              if (t.status == TripStatus.inProgress && t.myStopArrived == null && !t.boarded)
                Text(relative(big), style: TransitType.subheading),
              if (t.status == TripStatus.inProgress && t.nextStopName != null && t.boarded)
                Flexible(
                  child: Text(
                    'Next: ${t.nextStopName}',
                    style: TransitType.subheading,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

/// Where the bus is right now, with my stop marked and how far away it is.
class _BusMap extends ConsumerWidget {
  const _BusMap({required this.tripId, required this.myStopId});

  final int tripId;
  final int myStopId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final live = ref.watch(liveTripProvider(tripId)).asData?.value;
    if (live == null) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.fromLTRB(Space.gutter, 0, Space.gutter, Space.l),
      child: LiveMap(trips: [live], myStopId: myStopId, followTripId: tripId),
    );
  }
}

class _LiveLine extends ConsumerWidget {
  const _LiveLine({required this.tripId, required this.route, required this.myStopId, required this.delayMin});

  final int tripId;
  final TransitRoute route;
  final int myStopId;
  final int delayMin;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final trip = ref.watch(tripProvider(tripId));
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: Space.gutter),
      padding: const EdgeInsets.fromLTRB(0, Space.s, Space.m, Space.s),
      decoration: const BoxDecoration(color: TransitColors.white, borderRadius: Radii.signAll),
      child: AsyncBody(
        value: trip,
        builder: (t) => LineDiagram(
          color: route.color,
          showBus: t.running,
          stops: [
            for (final s in t.stops)
              LineStop(
                name: s.stopName,
                scheduled: s.scheduledAt,
                expected: s.scheduledAt.add(Duration(minutes: delayMin)),
                arrived: s.arrivedAt,
                isYou: s.stopId == myStopId,
                isTerminus: s == t.stops.last,
              ),
          ],
        ),
      ),
    );
  }
}

class _OtherTripRow extends StatelessWidget {
  const _OtherTripRow({required this.trip});

  final StudentTrip trip;

  @override
  Widget build(BuildContext context) {
    final t = trip;
    final status = switch (t.status) {
      TripStatus.completed => t.boarded ? 'Rode, boarded ${hm(t.boardedAt!)}' : 'Finished',
      TripStatus.cancelled => 'Cancelled',
      TripStatus.inProgress => 'Running',
      TripStatus.scheduled => 'Departs ${hm(t.scheduledDeparture)}',
    };
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: Space.gutter, vertical: Space.xs),
      padding: const EdgeInsets.all(Space.m),
      decoration: const BoxDecoration(color: TransitColors.white, borderRadius: Radii.signAll),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  t.isPickup ? 'Morning pickup' : 'Evening drop',
                  style: TransitType.subheading.copyWith(fontWeight: FontWeight.w800),
                ),
                Text(status, style: TransitType.small.copyWith(color: TransitColors.inkSoft)),
              ],
            ),
          ),
          if (t.myStopScheduled != null) Text(hm(t.myStopScheduled!), style: TransitType.figure),
        ],
      ),
    );
  }
}
