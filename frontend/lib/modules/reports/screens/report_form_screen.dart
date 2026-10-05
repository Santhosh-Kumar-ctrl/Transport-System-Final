import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/api/api_client.dart';
import '../../../design/design.dart';
import '../data/reports_api.dart';
import '../widgets/choice.dart';

/// Student: report a problem. Pick what kind, which trip, and say what happened in your own words.
class ReportFormScreen extends ConsumerStatefulWidget {
  const ReportFormScreen({super.key, this.tripId});

  /// Preselect this trip (from "My trips").
  final int? tripId;

  @override
  ConsumerState<ReportFormScreen> createState() => _ReportFormScreenState();
}

class _ReportFormScreenState extends ConsumerState<ReportFormScreen> {
  ReportKind? _kind;
  int? _tripId;
  var _tripPicked = false; // false until the student (or the preselect) has chosen
  var _anonymous = false;
  final _text = TextEditingController();
  var _busy = false;
  String? _error;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  String get _hint => switch (_kind) {
    ReportKind.lateness => 'e.g. The bus reached my stop about 20 minutes late',
    ReportKind.overcrowding => 'e.g. No seats, people standing at the door',
    ReportKind.safety => 'What happened, and roughly when',
    ReportKind.lostItem => 'What you lost: colour, type, where you sat',
    _ => 'What happened?',
  };

  Future<void> _send() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref
          .read(reportsActionsProvider)
          .submit(kind: _kind!, description: _text.text.trim(), tripId: _tripId, anonymous: _anonymous);
      ref.invalidate(myReportsProvider);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Report sent. The transport office will look into it and reply here.")),
      );
      context.go('/student/reports');
    } on ApiException catch (e) {
      setState(() {
        _error = e.message;
        _busy = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final options = ref.watch(tripOptionsProvider);
    final list = options.asData?.value ?? const <TripOption>[];
    if (!_tripPicked && options.hasValue) {
      // Preselect the trip passed in, else the newest one.
      final given = list.any((t) => t.tripId == widget.tripId) ? widget.tripId : null;
      _tripId = given ?? (list.isEmpty ? null : list.first.tripId);
      _tripPicked = true;
    }
    final ready = _kind != null && _text.text.trim().length >= 5 && !_busy;
    return Scaffold(
      body: Column(
        children: [
          SignHeader(
            title: 'Report a problem',
            subtitle: 'The transport office reads every report',
            leading: IconButton(
              tooltip: 'Back',
              icon: const Icon(Icons.arrow_back, color: TransitColors.white),
              onPressed: () => context.go('/student/reports'),
            ),
          ),
          Expanded(
            child: ListView(
              padding: EdgeInsets.fromLTRB(
                Space.gutter,
                Space.l,
                Space.gutter,
                MediaQuery.viewInsetsOf(context).bottom + Space.xl,
              ),
              children: [
                const Text('What happened?', style: TransitType.subheading),
                const SizedBox(height: Space.s),
                Wrap(
                  spacing: Space.s,
                  runSpacing: Space.s,
                  children: [
                    for (final k in ReportKind.values)
                      Choice(label: k.label, selected: _kind == k, onTap: () => setState(() => _kind = k)),
                  ],
                ),
                if (_kind == ReportKind.safety) ...[
                  const SizedBox(height: Space.m),
                  const SignNotice(
                    title: 'In danger right now?',
                    body: 'Call the college helpline or 112 first. Safety reports go to the transport office straight away.',
                    edge: TransitColors.late,
                  ),
                ],
                const SizedBox(height: Space.l),
                const Text('Which trip?', style: TransitType.subheading),
                const SizedBox(height: Space.s),
                AsyncBody(
                  value: options,
                  onRetry: () => ref.invalidate(tripOptionsProvider),
                  builder: (trips) => Wrap(
                    spacing: Space.s,
                    runSpacing: Space.s,
                    children: [
                      for (final t in trips.take(6))
                        Choice(
                          label: '${t.routeCode} · ${t.label}',
                          selected: _tripId == t.tripId,
                          onTap: () => setState(() => _tripId = t.tripId),
                        ),
                      Choice(
                        label: 'Not about a trip',
                        selected: _tripId == null,
                        onTap: () => setState(() => _tripId = null),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: Space.l),
                TextField(
                  controller: _text,
                  maxLines: 5,
                  maxLength: 1000,
                  decoration: InputDecoration(labelText: 'Tell us in your own words', hintText: _hint),
                  onChanged: (_) => setState(() {}),
                ),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  value: _anonymous,
                  onChanged: (v) => setState(() => _anonymous = v ?? false),
                  title: const Text('Hide my name from staff', style: TransitType.body),
                  subtitle: Text(
                    'Staff see what happened and on which trip, but not who sent it.',
                    style: TransitType.small.copyWith(color: TransitColors.inkSoft),
                  ),
                ),
                if (_error != null) ...[
                  const SizedBox(height: Space.s),
                  Text(_error!, style: TransitType.body.copyWith(color: TransitColors.late)),
                ],
                const SizedBox(height: Space.l),
                SignButton(
                  label: 'Send report',
                  expand: true,
                  height: 56,
                  busy: _busy,
                  onPressed: ready ? _send : null,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
