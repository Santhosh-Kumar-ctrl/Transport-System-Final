import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../../../core/api/api_client.dart';
import '../../../core/format.dart';
import '../../../design/design.dart';
import '../../dashboard/data/dashboard_api.dart';
import '../data/boarding_api.dart';

/// Plain-language guidance per backend error code.
(String, String) explainScanError(ApiException e) => switch (e.code) {
  'qr_expired' => (
    'That code has just changed',
    "The driver's code refreshes every 30 seconds. Scan the code on their screen again.",
  ),
  'qr_invalid' => ("That isn't a boarding code", "Scan the QR code shown on the driver's phone."),
  'already_boarded' => ("You're already on board", 'Your boarding on this bus is recorded. Nothing else to do.'),
  'bad_trip_state' => (
    "This bus isn't running",
    'Check you are boarding the right bus. The driver starts the trip before boarding opens.',
  ),
  _ => ("Couldn't record your boarding", e.message),
};

/// Student points the camera at the QR on the driver's phone.
class StudentScanScreen extends ConsumerStatefulWidget {
  const StudentScanScreen({super.key});

  @override
  ConsumerState<StudentScanScreen> createState() => _StudentScanScreenState();
}

class _StudentScanScreenState extends ConsumerState<StudentScanScreen> {
  final _scanner = MobileScannerController(formats: const [BarcodeFormat.qrCode]);
  final _paste = TextEditingController();
  var _busy = false;
  BoardingReceipt? _receipt;
  ApiException? _error;

  @override
  void dispose() {
    _scanner.dispose();
    _paste.dispose();
    super.dispose();
  }

  Future<void> _submit(String token) async {
    if (_busy || _receipt != null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    await _scanner.stop();
    try {
      final r = await ref.read(boardingActionsProvider).checkIn(token.trim());
      ref.invalidate(studentDashboardProvider);
      setState(() => _receipt = r);
    } on ApiException catch (e) {
      setState(() => _error = e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _retry() async {
    setState(() => _error = null);
    await _scanner.start();
  }

  @override
  Widget build(BuildContext context) {
    if (_receipt != null) return _Success(receipt: _receipt!);
    return Scaffold(
      backgroundColor: TransitColors.board,
      body: Column(
        children: [
          SignHeader(
            title: 'Scan to board',
            subtitle: "Point your camera at the QR code on the driver's phone",
            color: TransitColors.board,
            leading: IconButton(
              tooltip: 'Back',
              icon: const Icon(Icons.arrow_back, color: TransitColors.white),
              onPressed: () => context.pop(),
            ),
          ),
          Expanded(
            child: _error != null
                ? Center(
                    child: SingleChildScrollView(
                      child: SignNotice(
                        title: explainScanError(_error!).$1,
                        body: explainScanError(_error!).$2,
                        edge: _error!.code == 'already_boarded' ? TransitColors.go : TransitColors.late,
                        actionLabel: _error!.code == 'already_boarded' ? null : 'Scan again',
                        onAction: _retry,
                      ),
                    ),
                  )
                : Stack(
                    fit: StackFit.expand,
                    children: [
                      MobileScanner(
                        controller: _scanner,
                        onDetect: (capture) {
                          final raw = capture.barcodes.firstOrNull?.rawValue;
                          if (raw != null) _submit(raw);
                        },
                        errorBuilder: (context, error) => Center(
                          child: SignNotice(
                            title: 'Camera unavailable',
                            body: 'Allow camera access for this app, or paste the boarding code below.',
                            edge: TransitColors.caution,
                          ),
                        ),
                      ),
                      const _Viewfinder(),
                      if (_busy)
                        const ColoredBox(
                          color: Color(0x88000000),
                          child: Center(child: CircularProgressIndicator(color: TransitColors.led)),
                        ),
                    ],
                  ),
          ),
          // Development only: in a release build a pasted code (sent from someone on the bus) would let
          // a student board from anywhere within the code's 30 seconds.
          if (kDebugMode)
            Container(
              color: TransitColors.board,
              padding: const EdgeInsets.all(Space.gutter),
              child: SafeArea(
                top: false,
                child: Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _paste,
                        decoration: const InputDecoration(
                          labelText: 'No camera? Paste the boarding code',
                          isDense: true,
                        ),
                      ),
                    ),
                    const SizedBox(width: Space.s),
                    SignButton(label: 'Board', height: 46, onPressed: () => _submit(_paste.text)),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _Viewfinder extends StatelessWidget {
  const _Viewfinder();

  @override
  Widget build(BuildContext context) => IgnorePointer(
    child: Center(
      child: Container(
        width: 240,
        height: 240,
        decoration: BoxDecoration(
          border: Border.all(color: TransitColors.led, width: 4),
          borderRadius: Radii.signAll,
        ),
      ),
    ),
  );
}

class _Success extends StatelessWidget {
  const _Success({required this.receipt});

  final BoardingReceipt receipt;

  @override
  Widget build(BuildContext context) {
    final r = receipt;
    return Scaffold(
      backgroundColor: TransitColors.board,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(Space.gutter),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TicketStub(
                  routeCode: r.routeCode,
                  routeColor: TransitColors.parseHex(r.routeColor),
                  routeName: r.routeName,
                  registration: r.registration,
                  time: hm(r.boardedAt),
                  stopName: r.stopName,
                  message: r.message,
                  onRoute: r.allocationMatch,
                ),
                const SizedBox(height: Space.xl),
                ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 420),
                  child: SignButton(
                    label: 'Done',
                    kind: SignButtonKind.onDark,
                    expand: true,
                    onPressed: () => context.pop(),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
