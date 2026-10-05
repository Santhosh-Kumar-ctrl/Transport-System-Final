import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:transit/core/api/api_client.dart';
import 'package:transit/core/format.dart';
import 'package:transit/design/design.dart';
import 'package:transit/modules/dashboard/data/dashboard_api.dart';
import 'package:transit/modules/dashboard/screens/student_home_screen.dart';
import 'package:transit/modules/reports/data/reports_api.dart';
import 'package:transit/modules/tracking/data/tracking_api.dart';
import 'package:transit/modules/tracking/widgets/live_map.dart';
import 'package:transit/modules/trips/data/trip_models.dart';

import 'support/fakes.dart';

void main() {
  test('number plates are spaced like real plates', () {
    expect(NumberPlate.format('TN09AB1401'), 'TN 09 AB 1401');
    expect(NumberPlate.format('ka01f22'), 'KA 01 F 22');
    expect(NumberPlate.format('BUS-7'), 'BUS-7'); // unknown format left alone
  });

  test('delay labels', () {
    expect(delayLabel(12), '+12 min');
    expect(delayLabel(0), 'On time');
    expect(delayLabel(-1), 'On time');
    expect(delayLabel(-3), '3 min early');
  });

  test('route colours pick readable text', () {
    expect(TransitColors.onRoute(TransitColors.parseHex('#0B5CAD')), TransitColors.white);
    expect(TransitColors.onRoute(TransitColors.parseHex('#FFB81C')), TransitColors.ink);
  });

  test('trip status parses the API spelling', () {
    expect(TripStatus.parse('in_progress'), TripStatus.inProgress);
    expect(TripStatus.parse('completed'), TripStatus.completed);
  });

  test('student home focuses the running trip over later ones', () {
    final dash = StudentDashboard.fromJson(studentDashboardJson());
    expect(StudentHomeScreen.focusTrip(dash.trips)!.tripId, 7);
    expect(dash.myStopId, 3);
    expect(dash.route!.stops.last.stop.name, 'Main Gate (Campus)');
  });

  test('trip JSON maps next stop and delay', () {
    final t = TripDetail.fromJson(tripJson(delay: 8));
    expect(t.running, isTrue);
    expect(t.nextStop!.stopName, 'Koyambedu Market');
    expect(t.headline, 'To Main Gate (Campus)');
  });

  test('API errors keep the backend message and code', () {
    final req = RequestOptions(path: '/boarding/check-in');
    final e = ApiException.fromDio(
      DioException(
        requestOptions: req,
        response: Response(
          requestOptions: req,
          statusCode: 422,
          data: {'detail': 'This QR code has expired.', 'code': 'qr_expired'},
        ),
      ),
    );
    expect(e.code, 'qr_expired');
    expect(e.message, 'This QR code has expired.');

    final v = ApiException.fromDio(
      DioException(
        requestOptions: req,
        response: Response(
          requestOptions: req,
          statusCode: 422,
          data: {
            'detail': [
              {
                'loc': ['body', 'email'],
                'msg': 'Value error, student profile is required for role=student',
              },
            ],
          },
        ),
      ),
    );
    expect(v.message, 'student profile is required for role=student');

    final offline = ApiException.fromDio(DioException(requestOptions: req));
    expect(offline.message, contains("Can't reach"));
  });

  test('live trip JSON maps stops, coordinates and the latest fix', () {
    final t = LiveTrip.fromJson(liveTripJson());
    expect(t.running, isTrue);
    expect(t.stops.first.point, isNotNull);
    expect(t.stops.last.point, isNull); // a stop without coordinates stays off the map
    expect(t.position!.speedKmph, 28);
    expect(t.nextStopSequence, 3);
  });

  test('a late refetch never moves the bus backwards', () {
    final t = LiveTrip.fromJson(liveTripJson());
    final older = BusFix(
      tripId: 7,
      latitude: 1,
      longitude: 1,
      recordedAt: t.position!.recordedAt.subtract(const Duration(seconds: 30)),
    );
    final newer = BusFix(
      tripId: 7,
      latitude: 2,
      longitude: 2,
      recordedAt: t.position!.recordedAt.add(const Duration(seconds: 5)),
    );
    expect(t.withNewerPosition(older).position, same(t.position));
    expect(t.withNewerPosition(newer).position, same(newer));
    expect(t.withNewerPosition(null).position, same(t.position));
  });

  test('distances read like a sign', () {
    expect(distanceLabel(430), '450 m');
    expect(distanceLabel(1840), '1.8 km');
    expect(distanceLabel(12400), '12 km');
  });

  test('reports map the agent analysis for admins', () {
    final r = Report.fromJson(reportJson());
    expect(r.kind, ReportKind.lostItem);
    expect(r.kind.api, 'lost_item');
    expect(r.studentName, 'Priya R');
    expect(r.tripLabel, contains('morning pickup'));
    expect(r.messages.single.fromStaff, isTrue);
    expect(r.analysis!.findings.map((f) => f.verdictLabel), ['Confirmed', 'Partly']);
    expect(r.analysis!.findings.last.checkLabel, 'Found items');
    expect(r.analysis!.candidates.single.foundItemId, 3);
    expect(r.analysedAt, isNotNull);
  });

  test("a student's view of a report has no analysis and may be anonymous", () {
    final r = Report.fromJson(reportJson(anonymous: true, analysed: false));
    expect(r.studentName, isNull);
    expect(r.analysis, isNull);
    expect(r.analysisStatus, 'pending');
    expect(ReportKind.parse('lost_item'), ReportKind.lostItem);
    expect(ReportKind.parse('safety'), ReportKind.safety);
  });
}
