import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/api/api_client.dart';
import '../../../core/format.dart';
import '../../../design/design.dart';
import '../data/reports_api.dart';
import '../widgets/choice.dart';

/// Student: the reports they've sent and where each one stands.
class MyReportsScreen extends ConsumerWidget {
  const MyReportsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final reports = ref.watch(myReportsProvider);
    return Column(
      children: [
        SignHeader(
          title: 'My reports',
          subtitle: 'Problems you told the transport office about',
          trailing: TextButton.icon(
            style: TextButton.styleFrom(foregroundColor: TransitColors.white),
            onPressed: () => context.go('/student/reports/new'),
            icon: const Icon(Icons.add),
            label: const Text('New'),
          ),
        ),
        Expanded(
          child: AsyncBody(
            value: reports,
            onRetry: () => ref.invalidate(myReportsProvider),
            builder: (list) => list.isEmpty
                ? SignNotice(
                    title: 'No reports yet',
                    body: 'Bus late, overcrowded, unsafe, or lost something? Tell the transport office.',
                    actionLabel: 'Report a problem',
                    onAction: () => context.go('/student/reports/new'),
                  )
                : RefreshIndicator(
                    onRefresh: () async => ref.invalidate(myReportsProvider),
                    child: ListView.separated(
                      padding: const EdgeInsets.all(Space.gutter),
                      itemCount: list.length,
                      separatorBuilder: (_, _) => const Divider(),
                      itemBuilder: (_, i) => _Row(r: list[i]),
                    ),
                  ),
          ),
        ),
      ],
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.r});

  final Report r;

  @override
  Widget build(BuildContext context) {
    final (label, tone) = reportStatusPlate(r.status);
    return InkWell(
      onTap: () => context.go('/student/reports/${r.id}'),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: Space.m),
        child: Row(
          children: [
            if (r.routeCode != null && r.routeColor != null) ...[
              RouteBadge(code: r.routeCode!, color: TransitColors.parseHex(r.routeColor!), size: 36),
              const SizedBox(width: Space.m),
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(r.kind.label, style: TransitType.subheading.copyWith(fontWeight: FontWeight.w800)),
                  Text(
                    r.description,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TransitType.body.copyWith(color: TransitColors.inkSoft),
                  ),
                  Text(
                    'Sent ${dayLabel(r.createdAt)} ${hm(r.createdAt)}',
                    style: TransitType.small.copyWith(color: TransitColors.inkSoft),
                  ),
                ],
              ),
            ),
            const SizedBox(width: Space.s),
            StatusPlate(label, tone: tone),
          ],
        ),
      ),
    );
  }
}

/// Student: one report, the conversation with the transport office, and a box to add more.
class StudentReportScreen extends ConsumerStatefulWidget {
  const StudentReportScreen({super.key, required this.reportId});

  final int reportId;

  @override
  ConsumerState<StudentReportScreen> createState() => _StudentReportScreenState();
}

class _StudentReportScreenState extends ConsumerState<StudentReportScreen> {
  final _text = TextEditingController();
  var _busy = false;
  String? _error;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(reportsActionsProvider).followUp(widget.reportId, _text.text.trim());
      _text.clear();
      ref.invalidate(reportProvider(widget.reportId));
      ref.invalidate(myReportsProvider);
    } on ApiException catch (e) {
      _error = e.message;
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final report = ref.watch(reportProvider(widget.reportId));
    return Column(
      children: [
        SignHeader(
          title: report.asData?.value.kind.label ?? 'Report',
          subtitle: report.asData?.value.tripLabel,
          leading: IconButton(
            tooltip: 'Back',
            icon: const Icon(Icons.arrow_back, color: TransitColors.white),
            onPressed: () => context.go('/student/reports'),
          ),
        ),
        Expanded(
          child: AsyncBody(
            value: report,
            onRetry: () => ref.invalidate(reportProvider(widget.reportId)),
            builder: (r) {
              final (label, tone) = reportStatusPlate(r.status);
              return ListView(
                padding: const EdgeInsets.all(Space.gutter),
                children: [
                  Row(
                    children: [
                      StatusPlate(label, tone: tone),
                      const SizedBox(width: Space.s),
                      if (r.anonymous)
                        Text('Name hidden from staff', style: TransitType.small.copyWith(color: TransitColors.inkSoft)),
                    ],
                  ),
                  const SizedBox(height: Space.m),
                  _Bubble(fromStaff: false, body: r.description, at: r.createdAt),
                  for (final m in r.messages) _Bubble(fromStaff: m.fromStaff, body: m.body, at: m.createdAt),
                  if (r.closed) ...[
                    const SizedBox(height: Space.m),
                    SignNotice(
                      title: 'This report is closed',
                      body: r.resolutionNote ?? 'Send a new report if something else comes up.',
                    ),
                  ] else ...[
                    const SizedBox(height: Space.l),
                    TextField(
                      controller: _text,
                      maxLines: 3,
                      maxLength: 1000,
                      decoration: const InputDecoration(labelText: 'Add something'),
                      onChanged: (_) => setState(() {}),
                    ),
                    if (_error != null) Text(_error!, style: TransitType.body.copyWith(color: TransitColors.late)),
                    const SizedBox(height: Space.s),
                    SignButton(
                      label: 'Send',
                      expand: true,
                      busy: _busy,
                      onPressed: _text.text.trim().isEmpty || _busy ? null : _send,
                    ),
                  ],
                ],
              );
            },
          ),
        ),
      ],
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.fromStaff, required this.body, required this.at});

  final bool fromStaff;
  final String body;
  final DateTime at;

  @override
  Widget build(BuildContext context) => Container(
    margin: const EdgeInsets.only(bottom: Space.s),
    padding: const EdgeInsets.all(Space.m),
    decoration: BoxDecoration(
      color: fromStaff ? TransitColors.white : TransitColors.enamelDeep,
      borderRadius: Radii.signAll,
      border: Border(left: BorderSide(color: fromStaff ? TransitColors.signBlue : TransitColors.rule, width: 5)),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '${fromStaff ? 'Transport office' : 'You'} · ${dayLabel(at)} ${hm(at)}',
          style: TransitType.small.copyWith(color: TransitColors.inkSoft, fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 2),
        Text(body, style: TransitType.body),
      ],
    ),
  );
}
