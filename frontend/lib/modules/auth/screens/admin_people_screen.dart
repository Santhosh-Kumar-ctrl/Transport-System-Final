import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/auth/session.dart';
import '../../../design/design.dart';
import '../data/people_api.dart';

class AdminPeopleScreen extends ConsumerStatefulWidget {
  const AdminPeopleScreen({super.key});

  @override
  ConsumerState<AdminPeopleScreen> createState() => _AdminPeopleScreenState();
}

class _AdminPeopleScreenState extends ConsumerState<AdminPeopleScreen> {
  Role? _role = Role.student;
  var _q = '';

  @override
  Widget build(BuildContext context) {
    final query = (role: _role, q: _q);
    final people = ref.watch(peopleProvider(query));
    return Column(
      children: [
        SignHeader(
          title: 'People',
          subtitle: 'Students, drivers and staff accounts',
          trailing: SignButton(
            label: 'Add person',
            icon: Icons.person_add_alt,
            kind: SignButtonKind.onDark,
            height: 44,
            onPressed: () async {
              final created = await showDialog<bool>(context: context, builder: (_) => const _AddPersonDialog());
              if (created == true) ref.invalidate(peopleProvider);
            },
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(Space.gutter, Space.l, Space.gutter, Space.s),
          child: Wrap(
            spacing: Space.s,
            runSpacing: Space.s,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              for (final r in [Role.student, Role.driver, Role.admin, null])
                _Chip(
                  label: r == null ? 'Everyone' : '${r.name[0].toUpperCase()}${r.name.substring(1)}s',
                  selected: _role == r,
                  onTap: () => setState(() => _role = r),
                ),
              SizedBox(
                width: 260,
                child: TextField(
                  decoration: const InputDecoration(labelText: 'Search name, email or roll no', isDense: true),
                  onSubmitted: (v) => setState(() => _q = v.trim()),
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: AsyncBody(
            value: people,
            onRetry: () => ref.invalidate(peopleProvider(query)),
            builder: (list) => list.isEmpty
                ? const SignNotice(title: 'No one matches', body: 'Try another name or clear the search.')
                : ListView.separated(
                    padding: const EdgeInsets.symmetric(horizontal: Space.gutter, vertical: Space.s),
                    itemCount: list.length,
                    separatorBuilder: (_, _) => const Divider(),
                    itemBuilder: (_, i) => _PersonRow(person: list[i], query: query),
                  ),
          ),
        ),
      ],
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({required this.label, required this.selected, required this.onTap});

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => InkWell(
    onTap: onTap,
    borderRadius: Radii.signAll,
    child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: selected ? TransitColors.ink : TransitColors.white,
        borderRadius: Radii.signAll,
        border: Border.all(color: selected ? TransitColors.ink : TransitColors.rule, width: 1.5),
      ),
      child: Text(
        label,
        style: TransitType.small.copyWith(
          color: selected ? TransitColors.white : TransitColors.ink,
          fontWeight: FontWeight.w800,
        ),
      ),
    ),
  );
}

class _PersonRow extends ConsumerWidget {
  const _PersonRow({required this.person, required this.query});

  final Person person;
  final PeopleQuery query;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final p = person;
    final detail = [
      p.email,
      if (p.rollNo != null) p.rollNo!,
      if (p.department != null) p.department!,
      if (p.licenseNo != null) 'Licence ${p.licenseNo}',
      if (p.phone != null) p.phone!,
    ].join(', ');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: Space.m),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  p.fullName,
                  style: TransitType.subheading.copyWith(
                    color: p.isActive ? TransitColors.ink : TransitColors.inkSoft,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                Text(detail, style: TransitType.small.copyWith(color: TransitColors.inkSoft)),
              ],
            ),
          ),
          StatusPlate(p.role.name, tone: Tone.neutral),
          const SizedBox(width: Space.s),
          TextButton(
            onPressed: () async {
              await ref.read(peopleActionsProvider).setActive(p.id, !p.isActive);
              ref.invalidate(peopleProvider(query));
            },
            child: Text(p.isActive ? 'Deactivate' : 'Reactivate'),
          ),
        ],
      ),
    );
  }
}

class _AddPersonDialog extends ConsumerStatefulWidget {
  const _AddPersonDialog();

  @override
  ConsumerState<_AddPersonDialog> createState() => _AddPersonDialogState();
}

class _AddPersonDialogState extends ConsumerState<_AddPersonDialog> {
  final _form = GlobalKey<FormState>();
  var _role = Role.student;
  final _name = TextEditingController();
  final _email = TextEditingController();
  final _phone = TextEditingController();
  final _password = TextEditingController();
  final _roll = TextEditingController();
  final _dept = TextEditingController();
  final _license = TextEditingController();
  var _busy = false;
  String? _error;

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    setState(() => _busy = true);
    try {
      await ref.read(peopleActionsProvider).create({
        'full_name': _name.text.trim(),
        'email': _email.text.trim(),
        'phone': _phone.text.trim().isEmpty ? null : _phone.text.trim(),
        'password': _password.text,
        'role': _role.name,
        if (_role == Role.student)
          'student': {'roll_no': _roll.text.trim(), 'department': _dept.text.trim().isEmpty ? null : _dept.text.trim()},
        if (_role == Role.driver) 'driver': {'license_no': _license.text.trim()},
      });
      if (mounted) Navigator.pop(context, true);
    } on ApiException catch (e) {
      setState(() {
        _error = e.message;
        _busy = false;
      });
    }
  }

  String? _required(String? v) => (v == null || v.trim().isEmpty) ? 'Required' : null;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Add person', style: TransitType.heading),
      content: SizedBox(
        width: 420,
        child: Form(
          key: _form,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                DropdownButtonFormField<Role>(
                  initialValue: _role,
                  decoration: const InputDecoration(labelText: 'Role'),
                  items: [
                    for (final r in [Role.student, Role.driver, Role.admin])
                      DropdownMenuItem(value: r, child: Text(r.name)),
                  ],
                  onChanged: (r) => setState(() => _role = r!),
                ),
                const SizedBox(height: Space.m),
                TextFormField(
                  controller: _name,
                  decoration: const InputDecoration(labelText: 'Full name'),
                  validator: _required,
                ),
                const SizedBox(height: Space.m),
                TextFormField(
                  controller: _email,
                  decoration: const InputDecoration(labelText: 'Email'),
                  validator: _required,
                ),
                const SizedBox(height: Space.m),
                TextFormField(
                  controller: _phone,
                  decoration: const InputDecoration(labelText: 'Phone (optional)'),
                ),
                const SizedBox(height: Space.m),
                TextFormField(
                  controller: _password,
                  decoration: const InputDecoration(labelText: 'Temporary password'),
                  validator: (v) => (v ?? '').length < 8 ? 'At least 8 characters' : null,
                ),
                if (_role == Role.student) ...[
                  const SizedBox(height: Space.m),
                  TextFormField(
                    controller: _roll,
                    decoration: const InputDecoration(labelText: 'Roll no'),
                    validator: _required,
                  ),
                  const SizedBox(height: Space.m),
                  TextFormField(
                    controller: _dept,
                    decoration: const InputDecoration(labelText: 'Department (optional)'),
                  ),
                ],
                if (_role == Role.driver) ...[
                  const SizedBox(height: Space.m),
                  TextFormField(
                    controller: _license,
                    decoration: const InputDecoration(labelText: 'Licence no'),
                    validator: _required,
                  ),
                ],
                if (_error != null) ...[
                  const SizedBox(height: Space.m),
                  Text(_error!, style: TransitType.body.copyWith(color: TransitColors.late)),
                ],
              ],
            ),
          ),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        SignButton(label: 'Add person', onPressed: _save, busy: _busy, height: 44),
      ],
    );
  }
}
