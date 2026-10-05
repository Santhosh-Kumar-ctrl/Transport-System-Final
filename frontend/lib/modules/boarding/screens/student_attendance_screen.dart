import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/format.dart';
import '../../../design/design.dart';
import '../data/boarding_api.dart';

class StudentAttendanceScreen extends ConsumerWidget {
  const StudentAttendanceScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final items = ref.watch(myAttendanceProvider);
    return Column(
      children: [
        SignHeader(title: 'My trips', subtitle: items.asData == null ? null : _summary(items.asData!.value)),
        Expanded(
          child: AsyncBody(
            value: items,
            onRetry: () => ref.invalidate(myAttendanceProvider),
            builder: (list) => list.isEmpty
                ? const SignNotice(
                    title: 'No trips yet',
                    body: 'After each bus trip ends, it shows here as ridden or missed.',
                  )
                : ListView.separated(
                    padding: const EdgeInsets.all(Space.gutter),
                    itemCount: list.length,
                    separatorBuilder: (_, _) => const Divider(),
                    itemBuilder: (_, i) => _Row(item: list[i]),
                  ),
          ),
        ),
      ],
    );
  }

  static String _summary(List<AttendanceItem> list) {
    final rode = list.where((a) => a.status != 'absent').length;
    return list.isEmpty ? 'Nothing recorded yet' : 'Rode $rode of the last ${list.length} trips';
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.item});

  final AttendanceItem item;

  @override
  Widget build(BuildContext context) {
    final a = item;
    final (label, tone) = switch (a.status) {
      'present' => ('Rode', Tone.go),
      'present_unallocated' => ('Rode another route', Tone.caution),
      _ => ('Missed', Tone.late),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: Space.m),
      child: Row(
        children: [
          RouteBadge(code: a.routeCode, color: TransitColors.parseHex(a.routeColor), size: 36),
          const SizedBox(width: Space.m),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(dayLabel(a.date), style: TransitType.subheading.copyWith(fontWeight: FontWeight.w800)),
                Text(
                  '${a.direction == 'pickup' ? 'Morning pickup' : 'Evening drop'}'
                  '${a.boardedAt != null ? ', boarded ${hm(a.boardedAt!)}' : ''}',
                  style: TransitType.small.copyWith(color: TransitColors.inkSoft),
                ),
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              StatusPlate(label, tone: tone),
              TextButton(
                style: TextButton.styleFrom(
                  padding: EdgeInsets.zero,
                  minimumSize: const Size(0, 32),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
                onPressed: () => context.go('/student/reports/new?trip=${a.tripId}'),
                child: const Text('Report a problem'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
