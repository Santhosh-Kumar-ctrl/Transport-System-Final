import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../design/design.dart';
import '../data/reports_api.dart';
import 'choice.dart';

Future<void> showLogFoundItemSheet(BuildContext context, {required int tripId}) => showModalBottomSheet(
  context: context,
  isScrollControlled: true,
  builder: (_) => _LogFoundItemSheet(tripId: tripId),
);

/// Driver: something was left on the bus. A short description is enough for the office to
/// match it to a student's lost-item report.
class _LogFoundItemSheet extends ConsumerStatefulWidget {
  const _LogFoundItemSheet({required this.tripId});

  final int tripId;

  @override
  ConsumerState<_LogFoundItemSheet> createState() => _LogFoundItemSheetState();
}

class _LogFoundItemSheetState extends ConsumerState<_LogFoundItemSheet> {
  static const _common = ['Water bottle', 'Umbrella', 'Phone', 'ID card', 'Bag', 'Earphones', 'Lunch box'];
  final _text = TextEditingController();
  var _busy = false;
  String? _error;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(reportsActionsProvider).logFoundItem(description: _text.text.trim(), tripId: widget.tripId);
      if (!mounted) return;
      Navigator.pop(context);
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Logged. Hand the item in at the transport office.')));
    } on ApiException catch (e) {
      setState(() {
        _error = e.message;
        _busy = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final ready = _text.text.trim().length >= 3;
    return Padding(
      padding: EdgeInsets.fromLTRB(
        Space.gutter,
        Space.xl,
        Space.gutter,
        MediaQuery.viewInsetsOf(context).bottom + Space.xl,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Found something?', style: TransitType.title),
          const SizedBox(height: Space.xs),
          Text(
            'Describe it so the owner can recognise it: colour, brand, where it was.',
            style: TransitType.body.copyWith(color: TransitColors.inkSoft),
          ),
          const SizedBox(height: Space.l),
          Wrap(
            spacing: Space.s,
            runSpacing: Space.s,
            children: [
              for (final c in _common)
                Choice(
                  label: c,
                  small: true,
                  selected: _text.text.toLowerCase().startsWith(c.toLowerCase()),
                  onTap: () => setState(() {
                    _text.text = '$c, ';
                    _text.selection = TextSelection.collapsed(offset: _text.text.length);
                  }),
                ),
            ],
          ),
          const SizedBox(height: Space.m),
          TextField(
            controller: _text,
            maxLength: 300,
            decoration: const InputDecoration(labelText: 'What is it?', hintText: 'e.g. Blue steel water bottle'),
            onChanged: (_) => setState(() {}),
          ),
          if (_error != null) Text(_error!, style: TransitType.body.copyWith(color: TransitColors.late)),
          const SizedBox(height: Space.l),
          SignButton(label: 'Log found item', expand: true, height: 56, busy: _busy, onPressed: ready ? _save : null),
        ],
      ),
    );
  }
}
