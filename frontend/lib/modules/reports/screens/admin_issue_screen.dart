import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/api/api_client.dart';
import '../../../core/format.dart';
import '../../../core/realtime/realtime.dart';
import '../../../design/design.dart';
import '../data/reports_api.dart';
import '../widgets/choice.dart';
import 'admin_issues_screen.dart';

/// Transport office: one report with the agent's evidence, its draft reply, and the decisions
/// only staff can make (send, match a found item, close).
class AdminIssueScreen extends ConsumerStatefulWidget {
  const AdminIssueScreen({super.key, required this.reportId});

  final int reportId;

  @override
  ConsumerState<AdminIssueScreen> createState() => _AdminIssueScreenState();
}

class _AdminIssueScreenState extends ConsumerState<AdminIssueScreen> {
  final _reply = TextEditingController();
  DateTime? _draftFor; // the analysis the reply box was last filled from
  String? _busy; // which action is running
  String? _error;

  @override
  void dispose() {
    _reply.dispose();
    super.dispose();
  }

  Future<void> _run(String what, Future<void> Function(ReportsActions a) action, {String? done}) async {
    setState(() {
      _busy = what;
      _error = null;
    });
    try {
      await action(ref.read(reportsActionsProvider));
      ref.invalidate(reportProvider(widget.reportId));
      ref.invalidate(adminReportsProvider);
      ref.invalidate(foundItemsProvider);
      if (done != null && mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(done)));
    } on ApiException catch (e) {
      _error = e.message;
    }
    if (mounted) setState(() => _busy = null);
  }

  Future<void> _close(Report r) async {
    final note = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Close this report?'),
        content: TextField(
          controller: note,
          maxLength: 255,
          decoration: const InputDecoration(labelText: 'Note to the student (optional)'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Keep open')),
          SignButton(label: 'Close report', height: 44, onPressed: () => Navigator.pop(ctx, true)),
        ],
      ),
    );
    final text = note.text.trim();
    note.dispose();
    if (ok == true) {
      await _run('close', (a) => a.close(r.id, note: text.isEmpty ? null : text), done: 'Report closed');
    }
  }

  @override
  Widget build(BuildContext context) {
    final report = ref.watch(reportProvider(widget.reportId));
    listenLive(ref, (m) {
      if (m.payload['report_id'] == widget.reportId) ref.invalidate(reportProvider(widget.reportId));
    }, events: reportEvents);
    final r = report.asData?.value;
    // Fill the reply box with the agent's draft once, and again only if a re-check changes it.
    if (r != null && r.analysis != null && r.analysedAt != _draftFor && !r.closed) {
      _draftFor = r.analysedAt;
      _reply.text = r.analysis!.draftReply;
    }
    return Column(
      children: [
        SignHeader(
          title: r?.kind.label ?? 'Report',
          subtitle: r == null ? null : '${r.anonymous ? 'Anonymous' : r.studentName ?? ''} · ${r.tripLabel}',
          leading: IconButton(
            tooltip: 'Back',
            icon: const Icon(Icons.arrow_back, color: TransitColors.white),
            onPressed: () => context.go('/admin/issues'),
          ),
        ),
        Expanded(
          child: AsyncBody(
            value: report,
            onRetry: () => ref.invalidate(reportProvider(widget.reportId)),
            builder: (r) => ListView(
              padding: const EdgeInsets.all(Space.gutter),
              children: [
                _Facts(r: r),
                const SizedBox(height: Space.l),
                const Text('What the student wrote', style: TransitType.subheading),
                const SizedBox(height: Space.xs),
                Text(r.description, style: TransitType.body),
                for (final m in r.messages) ...[
                  const SizedBox(height: Space.s),
                  Text(
                    '${m.fromStaff ? 'Transport office' : 'Student'} · ${dayLabel(m.createdAt)} ${hm(m.createdAt)}',
                    style: TransitType.small.copyWith(color: TransitColors.inkSoft, fontWeight: FontWeight.w700),
                  ),
                  Text(m.body, style: TransitType.body),
                ],
                const SizedBox(height: Space.l),
                _AgentPanel(
                  r: r,
                  busy: _busy == 'reanalyse',
                  onRecheck: () => _run('reanalyse', (a) => a.reanalyse(r.id)),
                ),
                if (r.analysis != null && r.analysis!.candidates.isNotEmpty && r.matchedFoundItemId == null) ...[
                  const SizedBox(height: Space.l),
                  const Text('Possible matches in found items', style: TransitType.subheading),
                  for (final c in r.analysis!.candidates)
                    Padding(
                      padding: const EdgeInsets.only(top: Space.s),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(
                              '${c.description}${c.registration != null ? ' (${c.registration})' : ''}',
                              style: TransitType.body,
                            ),
                          ),
                          SignButton(
                            label: 'Match',
                            kind: SignButtonKind.quiet,
                            height: 40,
                            busy: _busy == 'match${c.foundItemId}',
                            onPressed: () => _run(
                              'match${c.foundItemId}',
                              (a) => a.match(r.id, c.foundItemId),
                              done: 'Matched. The student was told to collect it.',
                            ),
                          ),
                        ],
                      ),
                    ),
                ],
                if (r.matchedFoundItemId != null) ...[
                  const SizedBox(height: Space.m),
                  const SignNotice(title: 'Matched to a found item', body: 'The student was told to collect it.'),
                ],
                if (!r.closed) ...[
                  const SizedBox(height: Space.l),
                  const Text('Reply to the student', style: TransitType.subheading),
                  const SizedBox(height: Space.xs),
                  Text(
                    r.analysis == null
                        ? 'Write a reply.'
                        : 'Drafted by the assistant from the records. Check and edit it before sending.',
                    style: TransitType.small.copyWith(color: TransitColors.inkSoft),
                  ),
                  TextField(controller: _reply, maxLines: 5, maxLength: 1000),
                  if (_error != null) Text(_error!, style: TransitType.body.copyWith(color: TransitColors.late)),
                  const SizedBox(height: Space.s),
                  Row(
                    children: [
                      Expanded(
                        child: SignButton(
                          label: 'Send reply',
                          height: 52,
                          busy: _busy == 'reply',
                          onPressed: _busy != null
                              ? null
                              : () {
                                  final body = _reply.text.trim();
                                  if (body.isEmpty) return;
                                  _run('reply', (a) => a.reply(r.id, body), done: 'Reply sent');
                                },
                        ),
                      ),
                      const SizedBox(width: Space.s),
                      SignButton(
                        label: 'Close',
                        kind: SignButtonKind.quiet,
                        height: 52,
                        busy: _busy == 'close',
                        onPressed: _busy != null ? null : () => _close(r),
                      ),
                    ],
                  ),
                ] else
                  Padding(
                    padding: const EdgeInsets.only(top: Space.l),
                    child: SignNotice(title: 'Closed', body: r.resolutionNote ?? 'No closing note.'),
                  ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _Facts extends StatelessWidget {
  const _Facts({required this.r});

  final Report r;

  @override
  Widget build(BuildContext context) {
    final (label, tone) = reportStatusPlate(r.status);
    final severity = r.severity;
    return Wrap(
      spacing: Space.s,
      runSpacing: Space.s,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        if (r.routeCode != null && r.routeColor != null)
          RouteBadge(code: r.routeCode!, color: TransitColors.parseHex(r.routeColor!), size: 34),
        StatusPlate(label, tone: tone),
        if (severity != null)
          StatusPlate(
            switch (severity) {
              'critical' => 'Urgent',
              'high' => 'High',
              'low' => 'Low',
              _ => 'Normal',
            },
            tone: switch (severity) {
              'critical' => Tone.late,
              'high' => Tone.caution,
              _ => Tone.neutral,
            },
          ),
        Text(
          [
            if (r.registration != null) r.registration!,
            if (r.stopName != null) 'stop ${r.stopName}',
            if (r.rollNo != null) r.rollNo!,
            'sent ${dayLabel(r.createdAt)} ${hm(r.createdAt)}',
          ].join(', '),
          style: TransitType.small.copyWith(color: TransitColors.inkSoft),
        ),
      ],
    );
  }
}

/// The agent's work: what the records show, its summary and suggested next step.
class _AgentPanel extends StatelessWidget {
  const _AgentPanel({required this.r, required this.busy, required this.onRecheck});

  final Report r;
  final bool busy;
  final VoidCallback onRecheck;

  @override
  Widget build(BuildContext context) {
    final a = r.analysis;
    return Container(
      padding: const EdgeInsets.all(Space.m),
      decoration: BoxDecoration(
        color: TransitColors.white,
        borderRadius: Radii.signAll,
        border: Border.all(color: TransitColors.rule, width: 1.5),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: Text('What the records show', style: TransitType.subheading)),
              TextButton.icon(
                onPressed: busy || r.analysisStatus == 'pending' ? null : onRecheck,
                icon: const Icon(Icons.refresh, size: 18),
                label: const Text('Check again'),
              ),
            ],
          ),
          if (r.analysisStatus == 'pending' || busy)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: Space.s),
              child: Text(
                'Checking the trip data and drafting a reply…',
                style: TransitType.body.copyWith(color: TransitColors.inkSoft),
              ),
            )
          else if (r.analysisStatus == 'failed' || a == null)
            Text(
              "The check didn't finish. Try again, or read the report and reply yourself.",
              style: TransitType.body.copyWith(color: TransitColors.late),
            )
          else ...[
            for (final f in a.findings)
              Padding(
                padding: const EdgeInsets.only(top: Space.s),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 116,
                      child: StatusPlate(
                        f.verdictLabel,
                        tone: switch (f.verdict) {
                          'confirmed' => Tone.go,
                          'partly' => Tone.caution,
                          'not_supported' => Tone.late,
                          _ => Tone.neutral,
                        },
                      ),
                    ),
                    const SizedBox(width: Space.s),
                    Expanded(
                      child: Text.rich(
                        TextSpan(
                          children: [
                            TextSpan(
                              text: '${f.checkLabel}: ',
                              style: const TextStyle(fontWeight: FontWeight.w800),
                            ),
                            TextSpan(text: f.detail),
                          ],
                        ),
                        style: TransitType.body,
                      ),
                    ),
                  ],
                ),
              ),
            const Divider(height: Space.xl),
            Text(a.summary, style: TransitType.body),
            const SizedBox(height: Space.s),
            Text.rich(
              TextSpan(
                children: [
                  const TextSpan(
                    text: 'Suggested: ',
                    style: TextStyle(fontWeight: FontWeight.w800),
                  ),
                  TextSpan(text: a.suggestedAction),
                ],
              ),
              style: TransitType.body,
            ),
            const SizedBox(height: Space.s),
            Text(switch (r.analysedBy) {
              'rules' => 'Written by the rule-based checker (the AI model was not available).',
              final by? when by.endsWith('+rules') =>
                'Read by ${by.replaceFirst('+rules', '')}; the summary and draft are from the rule-based checker '
                    "because the AI's version wasn't usable.",
              final by => 'Drafted by $by. Verdicts come from the trip records, not the AI.',
            }, style: TransitType.small.copyWith(color: TransitColors.inkSoft)),
          ],
        ],
      ),
    );
  }
}
