import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/realtime/realtime.dart';
import '../../../design/design.dart';
import '../../reports/data/reports_api.dart';
import '../data/notifications_api.dart';

/// Wrap a role shell with this: live notifications refresh the inbox/badge
/// and show a short toast in the house style.
class LiveNotificationListener extends ConsumerWidget {
  const LiveNotificationListener({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    listenLive(ref, (m) {
      ref.invalidate(unreadCountProvider);
      ref.invalidate(inboxProvider);
      final n = AppNotification.fromJson(m.data);
      if (n.type.startsWith('Report') || n.type == 'LostItemMatched') {
        ref.invalidate(myReportsProvider);
        ref.invalidate(reportProvider);
      }
      final edge = switch (n.severity) {
        'critical' => TransitColors.late,
        'warning' => TransitColors.caution,
        _ => TransitColors.led,
      };
      ScaffoldMessenger.maybeOf(context)
        ?..hideCurrentSnackBar()
        ..showSnackBar(
          SnackBar(
            duration: const Duration(seconds: 6),
            content: Row(
              children: [
                Container(width: 5, height: 40, color: edge),
                const SizedBox(width: Space.m),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        n.title,
                        style: TransitType.subheading.copyWith(color: TransitColors.white, fontWeight: FontWeight.w800),
                      ),
                      if (n.body.isNotEmpty)
                        Text(
                          n.body,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: TransitType.small.copyWith(color: TransitColors.white.withValues(alpha: 0.8)),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        );
    }, events: {'notification'});
    return child;
  }
}
