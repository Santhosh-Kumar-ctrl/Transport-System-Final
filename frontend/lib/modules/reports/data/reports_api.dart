import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/format.dart';

/// What the student picks on the form. The agent works out the details from their words.
enum ReportKind {
  lateness('Late or skipped stop'),
  overcrowding('Overcrowding'),
  safety('Safety or conduct'),
  lostItem('Lost item'),
  other('Something else');

  const ReportKind(this.label);

  final String label;

  String get api => this == lostItem ? 'lost_item' : name;

  static ReportKind parse(String s) => s == 'lost_item' ? lostItem : ReportKind.values.byName(s);
}

class ReportMessage {
  const ReportMessage({required this.fromStaff, required this.body, required this.createdAt});

  final bool fromStaff;
  final String body;
  final DateTime createdAt;

  factory ReportMessage.fromJson(Json j) => ReportMessage(
    fromStaff: j['from_staff'] as bool,
    body: j['body'] as String,
    createdAt: parseTime(j['created_at'])!,
  );
}

/// One fact from the bus records, and whether it backs up the report.
class Finding {
  const Finding({required this.check, required this.verdict, required this.detail});

  final String check;
  final String verdict; // confirmed | partly | not_supported | no_data
  final String detail;

  factory Finding.fromJson(Json j) =>
      Finding(check: j['check'] as String, verdict: j['verdict'] as String, detail: j['detail'] as String);

  String get verdictLabel => switch (verdict) {
    'confirmed' => 'Confirmed',
    'partly' => 'Partly',
    'not_supported' => 'Not supported',
    _ => 'No data',
  };

  String get checkLabel => switch (check) {
    'on_this_trip' => 'On this trip',
    'lateness' => 'Timing',
    'stop_reached' => 'Stop reached',
    'trip_ran' => 'Trip ran',
    'crowding' => 'Seats',
    'speed' => 'Speed',
    'conduct' => 'Conduct',
    'lost_item' => 'Found items',
    _ => check,
  };
}

class MatchCandidate {
  const MatchCandidate({required this.foundItemId, required this.description, required this.score, this.registration});

  final int foundItemId;
  final String description;
  final double score;
  final String? registration;

  factory MatchCandidate.fromJson(Json j) => MatchCandidate(
    foundItemId: j['found_item_id'] as int,
    description: j['description'] as String,
    score: (j['score'] as num).toDouble(),
    registration: j['bus_registration_no'] as String?,
  );
}

class Analysis {
  const Analysis({
    required this.findings,
    required this.candidates,
    required this.summary,
    required this.suggestedAction,
    required this.draftReply,
  });

  final List<Finding> findings;
  final List<MatchCandidate> candidates;
  final String summary;
  final String suggestedAction;
  final String draftReply;

  factory Analysis.fromJson(Json j) => Analysis(
    findings: asList(j['findings']).map(Finding.fromJson).toList(),
    candidates: asList(j['match_candidates']).map(MatchCandidate.fromJson).toList(),
    summary: j['summary'] as String,
    suggestedAction: j['suggested_action'] as String,
    draftReply: j['draft_reply'] as String,
  );
}

/// A report as the student sees it. Admin-only fields are null in the student's view.
class Report {
  const Report({
    required this.id,
    required this.kind,
    required this.description,
    required this.status,
    required this.anonymous,
    required this.createdAt,
    required this.messages,
    this.tripId,
    this.serviceDate,
    this.direction,
    this.routeCode,
    this.routeColor,
    this.stopName,
    this.resolutionNote,
    this.studentName,
    this.rollNo,
    this.registration,
    this.severity,
    this.analysisStatus,
    this.analysis,
    this.analysedBy,
    this.analysedAt,
    this.matchedFoundItemId,
  });

  final int id;
  final ReportKind kind;
  final String description;
  final String status; // open | replied | closed
  final bool anonymous;
  final DateTime createdAt;
  final List<ReportMessage> messages;
  final int? tripId;
  final DateTime? serviceDate;
  final String? direction;
  final String? routeCode;
  final String? routeColor;
  final String? stopName;
  final String? resolutionNote;

  // Admin view only.
  final String? studentName;
  final String? rollNo;
  final String? registration;
  final String? severity; // low | normal | high | critical
  final String? analysisStatus; // pending | done | failed
  final Analysis? analysis;
  final String? analysedBy;
  final DateTime? analysedAt;
  final int? matchedFoundItemId;

  bool get closed => status == 'closed';

  String get tripLabel {
    if (serviceDate == null) return 'Not about a trip';
    final run = direction == 'drop' ? 'evening drop' : 'morning pickup';
    return '${dayLabel(serviceDate!)}, $run';
  }

  factory Report.fromJson(Json j) => Report(
    id: j['id'] as int,
    kind: ReportKind.parse(j['kind'] as String),
    description: j['description'] as String,
    status: j['status'] as String,
    anonymous: j['anonymous'] as bool,
    createdAt: parseTime(j['created_at'])!,
    messages: j['messages'] == null ? const [] : asList(j['messages']).map(ReportMessage.fromJson).toList(),
    tripId: j['trip_id'] as int?,
    serviceDate: j['service_date'] == null ? null : DateTime.parse(j['service_date'] as String),
    direction: j['direction'] as String?,
    routeCode: j['route_code'] as String?,
    routeColor: j['route_color'] as String?,
    stopName: j['stop_name'] as String?,
    resolutionNote: j['resolution_note'] as String?,
    studentName: j['student_name'] as String?,
    rollNo: j['roll_no'] as String?,
    registration: j['bus_registration_no'] as String?,
    severity: j['severity'] as String?,
    analysisStatus: j['analysis_status'] as String?,
    analysis: j['analysis'] == null ? null : Analysis.fromJson(j['analysis'] as Json),
    analysedBy: j['analysed_by'] as String?,
    analysedAt: parseTime(j['analysed_at']),
    matchedFoundItemId: j['matched_found_item_id'] as int?,
  );
}

/// One of the student's recent trips, for the "which trip?" picker.
class TripOption {
  const TripOption({
    required this.tripId,
    required this.serviceDate,
    required this.direction,
    required this.departure,
    required this.routeCode,
    required this.routeColor,
    required this.boarded,
  });

  final int tripId;
  final DateTime serviceDate;
  final String direction;
  final DateTime departure;
  final String routeCode;
  final String routeColor;
  final bool boarded;

  String get label => '${dayLabel(serviceDate)}, ${direction == 'drop' ? 'drop' : 'pickup'} ${hm(departure)}';

  factory TripOption.fromJson(Json j) => TripOption(
    tripId: j['trip_id'] as int,
    serviceDate: DateTime.parse(j['service_date'] as String),
    direction: j['direction'] as String,
    departure: parseTime(j['scheduled_departure'])!,
    routeCode: j['route_code'] as String,
    routeColor: j['route_color'] as String,
    boarded: j['boarded'] as bool,
  );
}

class FoundItem {
  const FoundItem({
    required this.id,
    required this.description,
    required this.status,
    required this.createdAt,
    this.routeCode,
    this.registration,
    this.loggedBy,
  });

  final int id;
  final String description;
  final String status; // unclaimed | matched | returned
  final DateTime createdAt;
  final String? routeCode;
  final String? registration;
  final String? loggedBy;

  factory FoundItem.fromJson(Json j) => FoundItem(
    id: j['id'] as int,
    description: j['description'] as String,
    status: j['status'] as String,
    createdAt: parseTime(j['created_at'])!,
    routeCode: j['route_code'] as String?,
    registration: j['bus_registration_no'] as String?,
    loggedBy: j['logged_by_name'] as String?,
  );
}

// ---------------- Providers ----------------
final myReportsProvider = FutureProvider.autoDispose<List<Report>>((ref) async {
  return asList(await ref.read(apiProvider).get('/reports/mine')).map(Report.fromJson).toList();
});

final reportProvider = FutureProvider.autoDispose.family<Report, int>((ref, id) async {
  return Report.fromJson(await ref.read(apiProvider).get<Json>('/reports/$id'));
});

typedef ReportsQuery = ({String? status, String? kind});

final adminReportsProvider = FutureProvider.autoDispose.family<List<Report>, ReportsQuery>((ref, q) async {
  final rows = await ref.read(apiProvider).get('/reports', query: {'status': q.status, 'kind': q.kind});
  return asList(rows).map(Report.fromJson).toList();
});

final tripOptionsProvider = FutureProvider.autoDispose<List<TripOption>>((ref) async {
  return asList(await ref.read(apiProvider).get('/reports/trip-options')).map(TripOption.fromJson).toList();
});

final foundItemsProvider = FutureProvider.autoDispose.family<List<FoundItem>, String?>((ref, status) async {
  return asList(await ref.read(apiProvider).get('/found-items', query: {'status': status}))
      .map(FoundItem.fromJson)
      .toList();
});

// ---------------- Actions ----------------
class ReportsActions {
  ReportsActions(this._api);

  final ApiClient _api;

  Future<Report> submit({
    required ReportKind kind,
    required String description,
    int? tripId,
    bool anonymous = false,
  }) async => Report.fromJson(
    await _api.post<Json>('/reports', {
      'kind': kind.api,
      'description': description,
      'trip_id': tripId,
      'anonymous': anonymous,
    }),
  );

  Future<void> followUp(int id, String body) => _api.post('/reports/$id/messages', {'body': body});

  Future<void> reply(int id, String body) => _api.post('/reports/$id/reply', {'body': body});

  Future<void> close(int id, {String? note}) => _api.post('/reports/$id/close', {'note': note});

  Future<void> reanalyse(int id) => _api.post('/reports/$id/reanalyse');

  Future<void> match(int id, int foundItemId) => _api.post('/reports/$id/match/$foundItemId');

  Future<void> logFoundItem({required String description, int? tripId}) =>
      _api.post('/found-items', {'description': description, 'trip_id': tripId});

  Future<void> markReturned(int foundItemId) => _api.patch('/found-items/$foundItemId', {'status': 'returned'});
}

final reportsActionsProvider = Provider((ref) => ReportsActions(ref.read(apiProvider)));
