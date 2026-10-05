import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/api/api_client.dart';
import '../../../core/format.dart';
import '../../../core/realtime/realtime.dart';
import '../../../design/design.dart';
import '../data/reports_api.dart';
import '../widgets/choice.dart';

/// Events that change what this screen shows (pushed to admins only by the reports module).
const reportEvents = {
  'ReportSubmitted',
  'ReportAnalysed',
  'ReportFollowUp',
  'ReportReplied',
  'ReportClosed',
  'LostItemMatched',
  'FoundItemLogged',
};

/// Transport office: problems students reported (with the agent's findings), and found items.
class AdminIssuesScreen extends ConsumerStatefulWidget {
  const AdminIssuesScreen({super.key});

  @override
  ConsumerState<AdminIssuesScreen> createState() => _AdminIssuesScreenState();
}

class _AdminIssuesScreenState extends ConsumerState<AdminIssuesScreen> {
  var _tab = 0;
  String? _status = 'open';
  String? _kind;

  @override
  Widget build(BuildContext context) {
    listenLive(ref, (_) {
      ref.invalidate(adminReportsProvider);
      ref.invalidate(foundItemsProvider);
    }, events: reportEvents);
    return Column(
      children: [
        SignHeader(
          title: 'Issues',
          subtitle: 'Problems students reported, checked against the bus records',
          bottom: Row(
            children: [
              _Tab('Reports', _tab == 0, () => setState(() => _tab = 0)),
              const SizedBox(width: Space.s),
              _Tab('Found items', _tab == 1, () => setState(() => _tab = 1)),
            ],
          ),
        ),
        Expanded(child: _tab == 0 ? _reports() : const _FoundItems()),
      ],
    );
  }

  Widget _reports() {
    final reports = ref.watch(adminReportsProvider((status: _status, kind: _kind)));
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(Space.gutter, Space.m, Space.gutter, 0),
          child: Wrap(
            spacing: Space.s,
            runSpacing: Space.s,
            children: [
              for (final (label, value) in [
                ('Open', 'open'),
                ('Replied', 'replied'),
                ('Closed', 'closed'),
                ('All', null),
              ])
                Choice(
                  label: label,
                  small: true,
                  selected: _status == value,
                  onTap: () => setState(() => _status = value),
                ),
              const SizedBox(width: Space.m),
              for (final k in [null, ...ReportKind.values])
                Choice(
                  label: k?.label ?? 'Any kind',
                  small: true,
                  selected: _kind == k?.api,
                  onTap: () => setState(() => _kind = k?.api),
                ),
            ],
          ),
        ),
        Expanded(
          child: AsyncBody(
            value: reports,
            onRetry: () => ref.invalidate(adminReportsProvider),
            builder: (list) => list.isEmpty
                ? const SignNotice(title: 'Nothing here', body: 'Reports students send show up here as they arrive.')
                : RefreshIndicator(
                    onRefresh: () async => ref.invalidate(adminReportsProvider),
                    child: ListView.builder(
                      padding: const EdgeInsets.all(Space.gutter),
                      itemCount: list.length,
                      itemBuilder: (_, i) => _IssueRow(r: list[i]),
                    ),
                  ),
          ),
        ),
      ],
    );
  }
}

Color severityColor(String? severity) => switch (severity) {
  'critical' => TransitColors.late,
  'high' => TransitColors.caution,
  _ => TransitColors.signBlue,
};

class _IssueRow extends StatelessWidget {
  const _IssueRow({required this.r});

  final Report r;

  @override
  Widget build(BuildContext context) {
    final (label, tone) = reportStatusPlate(r.status);
    final summary = r.analysisStatus == 'pending'
        ? 'Checking the bus records…'
        : (r.analysis?.summary ?? r.description);
    return InkWell(
      onTap: () => context.go('/admin/issues/${r.id}'),
      child: Container(
        margin: const EdgeInsets.only(bottom: Space.s),
        padding: const EdgeInsets.all(Space.m),
        decoration: BoxDecoration(
          color: r.closed ? TransitColors.enamelDeep : TransitColors.white,
          borderRadius: Radii.signAll,
          border: Border(left: BorderSide(color: r.closed ? TransitColors.rule : severityColor(r.severity), width: 5)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (r.routeCode != null && r.routeColor != null) ...[
              RouteBadge(code: r.routeCode!, color: TransitColors.parseHex(r.routeColor!), size: 34),
              const SizedBox(width: Space.m),
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '${r.kind.label}${r.severity == 'critical' ? ', urgent' : ''}',
                    style: TransitType.subheading.copyWith(fontWeight: FontWeight.w800),
                  ),
                  Text(summary, maxLines: 2, overflow: TextOverflow.ellipsis, style: TransitType.body),
                  Text(
                    '${r.anonymous ? 'Anonymous' : r.studentName ?? ''} · ${r.tripLabel}',
                    style: TransitType.small.copyWith(color: TransitColors.inkSoft),
                  ),
                ],
              ),
            ),
            const SizedBox(width: Space.s),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                StatusPlate(label, tone: tone),
                const SizedBox(height: Space.xs),
                Text(relative(r.createdAt), style: TransitType.small.copyWith(color: TransitColors.inkSoft)),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _FoundItems extends ConsumerStatefulWidget {
  const _FoundItems();

  @override
  ConsumerState<_FoundItems> createState() => _FoundItemsState();
}

class _FoundItemsState extends ConsumerState<_FoundItems> {
  String? _status = 'unclaimed';

  Future<void> _returned(FoundItem item) async {
    try {
      await ref.read(reportsActionsProvider).markReturned(item.id);
      ref.invalidate(foundItemsProvider);
    } on ApiException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final items = ref.watch(foundItemsProvider(_status));
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(Space.gutter, Space.m, Space.gutter, 0),
          child: Row(
            children: [
              Expanded(
                child: Wrap(
                  spacing: Space.s,
                  runSpacing: Space.s,
                  children: [
                    for (final (label, value) in [
                      ('Unclaimed', 'unclaimed'),
                      ('Matched', 'matched'),
                      ('Returned', 'returned'),
                      ('All', null),
                    ])
                      Choice(
                        label: label,
                        small: true,
                        selected: _status == value,
                        onTap: () => setState(() => _status = value),
                      ),
                  ],
                ),
              ),
              SignButton(
                label: 'Log item',
                icon: Icons.add,
                height: 40,
                onPressed: () => showLogFoundItemDialog(context, ref),
              ),
            ],
          ),
        ),
        Expanded(
          child: AsyncBody(
            value: items,
            onRetry: () => ref.invalidate(foundItemsProvider),
            builder: (list) => list.isEmpty
                ? const SignNotice(title: 'No items', body: 'Items drivers log as found on their bus show up here.')
                : ListView.separated(
                    padding: const EdgeInsets.all(Space.gutter),
                    itemCount: list.length,
                    separatorBuilder: (_, _) => const Divider(),
                    itemBuilder: (_, i) {
                      final f = list[i];
                      final (label, tone) = switch (f.status) {
                        'matched' => ('Matched', Tone.info),
                        'returned' => ('Returned', Tone.go),
                        _ => ('Unclaimed', Tone.caution),
                      };
                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: Space.s),
                        child: Row(
                          children: [
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    f.description,
                                    style: TransitType.subheading.copyWith(fontWeight: FontWeight.w800),
                                  ),
                                  Text(
                                    [
                                      if (f.routeCode != null) 'Route ${f.routeCode}',
                                      if (f.registration != null) f.registration!,
                                      if (f.loggedBy != null) 'logged by ${f.loggedBy}',
                                      '${dayLabel(f.createdAt)} ${hm(f.createdAt)}',
                                    ].join(', '),
                                    style: TransitType.small.copyWith(color: TransitColors.inkSoft),
                                  ),
                                ],
                              ),
                            ),
                            StatusPlate(label, tone: tone),
                            if (f.status == 'matched') ...[
                              const SizedBox(width: Space.s),
                              TextButton(onPressed: () => _returned(f), child: const Text('Returned')),
                            ],
                          ],
                        ),
                      );
                    },
                  ),
          ),
        ),
      ],
    );
  }
}

/// Admin: log an item handed in at the office (not tied to a trip).
Future<void> showLogFoundItemDialog(BuildContext context, WidgetRef ref) async {
  final text = TextEditingController();
  final saved = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('Log a found item'),
      content: TextField(
        controller: text,
        autofocus: true,
        maxLength: 300,
        decoration: const InputDecoration(labelText: 'What is it?', hintText: 'e.g. Blue steel water bottle'),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
        SignButton(
          label: 'Save',
          height: 44,
          onPressed: () async {
            if (text.text.trim().length < 3) return;
            try {
              await ref.read(reportsActionsProvider).logFoundItem(description: text.text.trim());
              if (ctx.mounted) Navigator.pop(ctx, true);
            } on ApiException catch (e) {
              if (ctx.mounted) ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(e.message)));
            }
          },
        ),
      ],
    ),
  );
  text.dispose();
  if (saved == true) ref.invalidate(foundItemsProvider);
}

class _Tab extends StatelessWidget {
  const _Tab(this.label, this.selected, this.onTap);

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => InkWell(
    onTap: onTap,
    child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: selected ? TransitColors.white : Colors.transparent,
        borderRadius: Radii.signAll,
        border: Border.all(color: TransitColors.white, width: 1.5),
      ),
      child: Text(
        label,
        style: TransitType.subheading.copyWith(
          color: selected ? TransitColors.signBlue : TransitColors.white,
          fontWeight: FontWeight.w800,
        ),
      ),
    ),
  );
}
