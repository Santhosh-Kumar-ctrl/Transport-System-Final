import 'package:flutter/material.dart';

import '../../../design/design.dart';

/// A tappable option chip: ink when selected, outlined otherwise.
class Choice extends StatelessWidget {
  const Choice({super.key, required this.label, required this.selected, required this.onTap, this.small = false});

  final String label;
  final bool selected;
  final VoidCallback onTap;
  final bool small;

  @override
  Widget build(BuildContext context) => InkWell(
    onTap: onTap,
    borderRadius: Radii.signAll,
    child: Container(
      padding: EdgeInsets.symmetric(horizontal: small ? 10 : 14, vertical: small ? 6 : 12),
      decoration: BoxDecoration(
        color: selected ? TransitColors.ink : TransitColors.white,
        borderRadius: Radii.signAll,
        border: Border.all(color: selected ? TransitColors.ink : TransitColors.rule, width: 1.5),
      ),
      child: Text(
        label,
        style: (small ? TransitType.small : TransitType.subheading).copyWith(
          color: selected ? TransitColors.white : TransitColors.ink,
          fontWeight: FontWeight.w800,
        ),
      ),
    ),
  );
}

/// Label and tone for a report's status, shared by the student and admin lists.
(String, Tone) reportStatusPlate(String status) => switch (status) {
  'replied' => ('Replied', Tone.info),
  'closed' => ('Closed', Tone.neutral),
  _ => ('Open', Tone.caution),
};
