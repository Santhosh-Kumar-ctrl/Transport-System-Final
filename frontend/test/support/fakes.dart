import 'dart:io';

import 'package:flutter/services.dart';
import 'package:transit/core/api/api_client.dart';

/// Loads the real app font (and Material icons) so rendered screenshots look like the app.
Future<void> loadAppFonts() async {
  final overpass = FontLoader('Overpass');
  for (final w in [400, 600, 800]) {
    overpass.addFont(File('assets/fonts/Overpass-$w.ttf').readAsBytes().then((b) => ByteData.sublistView(b)));
  }
  await overpass.load();
  final root = Platform.environment['FLUTTER_ROOT'] ?? '${Platform.environment['USERPROFILE']}/develop/flutter';
  final icons = File('$root/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf');
  if (icons.existsSync()) {
    await (FontLoader('MaterialIcons')..addFont(icons.readAsBytes().then((b) => ByteData.sublistView(b)))).load();
  }
}

final _base = DateTime.now().toUtc();
String at(int minutes) => _base.add(Duration(minutes: minutes)).toIso8601String();

const route14 = {'id': 1, 'code': '14', 'name': 'North Loop', 'color': '#0B5CAD'};

const _stopNames = [
  'Anna Nagar Roundtana',
  'Thirumangalam',
  'Koyambedu Market',
  'Vadapalani',
  'Ashok Pillar',
  'Main Gate (Campus)',
];
const _offsets = [0, 7, 15, 24, 31, 42];

/// A pickup trip that left 12 minutes ago, reached stop 2 eight minutes late.
Json tripJson({int delay = 8, String status = 'in_progress'}) {
  final stops = [
    for (var i = 0; i < _stopNames.length; i++)
      {
        'id': i + 1,
        'route_stop_id': i + 1,
        'stop_id': i + 1,
        'stop_name': _stopNames[i],
        'sequence': i + 1,
        'scheduled_at': at(-20 + _offsets[i]),
        'arrived_at': i == 0 ? at(-20) : (i == 1 ? at(-20 + _offsets[1] + delay) : null),
        'delay_min': i == 0 ? 0 : (i == 1 ? delay : null),
      },
  ];
  return {
    'id': 7,
    'schedule_id': 1,
    'route_id': 1,
    'bus_id': 1,
    'driver_id': 5,
    'direction': 'pickup',
    'service_date': _base.toIso8601String().substring(0, 10),
    'scheduled_departure': at(-20),
    'status': status,
    'started_at': at(-20),
    'ended_at': null,
    'current_delay_min': delay,
    'cancel_reason': null,
    'route': route14,
    'bus': {'id': 1, 'registration_no': 'TN09AB1401', 'capacity': 40},
    'driver': {'id': 5, 'full_name': 'Murugan K', 'phone': '9840011122'},
    'stops': stops,
    'next_stop': stops[2],
  };
}

Json studentDashboardJson() => {
  'service_date': _base.toIso8601String().substring(0, 10),
  'allocation': {
    'allocation': {
      'id': 3,
      'status': 'active',
      'valid_from': '2026-09-01',
      'ended_at': null,
      'end_reason': null,
      'route_stop_id': 3,
      'student': {'id': 30, 'full_name': 'Manoj K', 'roll_no': '22CSE027'},
      'route': route14,
      'stop': {'id': 3, 'name': 'Koyambedu Market'},
    },
    'route': {
      ...route14,
      'description': null,
      'is_active': true,
      'stops': [
        for (var i = 0; i < _stopNames.length; i++)
          {
            'id': i + 1,
            'stop_id': i + 1,
            'sequence': i + 1,
            'offset_min': _offsets[i],
            'stop': {'id': i + 1, 'name': _stopNames[i], 'landmark': null, 'latitude': null, 'longitude': null},
          },
      ],
    },
  },
  'trips': [
    {
      'trip_id': 7,
      'direction': 'pickup',
      'status': 'in_progress',
      'scheduled_departure': at(-20),
      'started_at': at(-20),
      'bus_registration_no': 'TN09AB1401',
      'delay_min': 8,
      'my_stop': {
        'stop_id': 3,
        'name': 'Koyambedu Market',
        'sequence': 3,
        'scheduled_at': at(-5),
        'expected_at': at(3),
        'arrived_at': null,
      },
      'next_stop_name': 'Koyambedu Market',
      'stops_done': 2,
      'stops_total': 6,
      'boarded': false,
      'boarded_at': null,
    },
    {
      'trip_id': 8,
      'direction': 'drop',
      'status': 'scheduled',
      'scheduled_departure': at(480),
      'started_at': null,
      'bus_registration_no': 'TN09AB1401',
      'delay_min': 0,
      'my_stop': {
        'stop_id': 3,
        'name': 'Koyambedu Market',
        'sequence': 4,
        'scheduled_at': at(507),
        'expected_at': at(507),
        'arrived_at': null,
      },
      'next_stop_name': null,
      'stops_done': 0,
      'stops_total': 6,
      'boarded': false,
      'boarded_at': null,
    },
  ],
  'unread_notifications': 2,
};

Json boardRow(
  int id,
  Json route, {
  required String status,
  int delay = 0,
  int boarded = 0,
  int cap = 40,
  String reg = 'TN09AB1401',
  String driver = 'Murugan K',
  int dep = -20,
  String? next,
  String dir = 'pickup',
}) => {
  'trip_id': id,
  'route': route,
  'direction': dir,
  'bus_registration_no': reg,
  'driver_name': driver,
  'status': status,
  'scheduled_departure': at(dep),
  'started_at': status == 'in_progress' ? at(dep) : null,
  'delay_min': delay,
  'next_stop': next == null
      ? null
      : {'sequence': 3, 'name': next, 'scheduled_at': at(dep + 20), 'expected_at': at(dep + 20 + delay)},
  'stops_total': 6,
  'stops_done': status == 'completed' ? 6 : (status == 'in_progress' ? 2 : 0),
  'boarded': boarded,
  'capacity': cap,
  'occupancy_level': boarded > cap ? 'over' : (boarded * 100 >= cap * 90 ? 'warning' : 'ok'),
  'allocated': 10,
};

Json adminDashboardJson() {
  const r7 = {'id': 2, 'code': '7', 'name': 'Lake Road', 'color': '#00857C'};
  const r22 = {'id': 3, 'code': '22', 'name': 'Hill View', 'color': '#8C1D40'};
  return {
    'service_date': _base.toIso8601String().substring(0, 10),
    'counts': {'scheduled': 3, 'in_progress': 2, 'completed': 1, 'cancelled': 0, 'delayed_now': 1},
    'board': [
      boardRow(7, route14, status: 'in_progress', delay: 12, boarded: 18, next: 'Koyambedu Market'),
      boardRow(
        9,
        r7,
        status: 'in_progress',
        delay: 1,
        boarded: 29,
        cap: 32,
        reg: 'TN09AB0702',
        driver: 'Selvi R',
        dep: -15,
        next: 'Adyar Depot',
      ),
      boardRow(10, r22, status: 'scheduled', reg: 'TN09AB2203', driver: 'Joseph A', dep: 25, cap: 50),
      boardRow(11, route14, status: 'scheduled', dep: 480, dir: 'drop'),
      boardRow(12, r7, status: 'scheduled', reg: 'TN09AB0702', driver: 'Selvi R', dep: 490, dir: 'drop', cap: 32),
      boardRow(6, r22, status: 'completed', boarded: 21, reg: 'TN09AB2203', driver: 'Joseph A', dep: -90, cap: 50),
    ],
    'alerts': [
      {
        'id': 40,
        'type': 'TripDelayed',
        'aggregate_type': 'trip',
        'aggregate_id': 7,
        'actor_id': null,
        'actor_name': null,
        'payload': {'route_id': 1, 'delay_min': 12, 'source': 'stop_arrival', 'at_stop_name': 'Thirumangalam'},
        'occurred_at': at(-4),
      },
      {
        'id': 38,
        'type': 'CapacityWarning',
        'aggregate_type': 'trip',
        'aggregate_id': 9,
        'actor_id': null,
        'actor_name': null,
        'payload': {'route_id': 2, 'boarded': 29, 'capacity': 32},
        'occurred_at': at(-6),
      },
      {
        'id': 31,
        'type': 'UnallocatedBoarding',
        'aggregate_type': 'trip',
        'aggregate_id': 7,
        'actor_id': null,
        'actor_name': null,
        'payload': {'route_id': 1, 'student_name': 'Ravi T'},
        'occurred_at': at(-11),
      },
    ],
    'utilization': [
      {
        'route_id': 1,
        'code': '14',
        'name': 'North Loop',
        'color': '#0B5CAD',
        'allocated': 10,
        'seat_capacity': 20,
        'pct': 50,
        'level': 'ok',
        'active_schedules': 3,
      },
      {
        'route_id': 2,
        'code': '7',
        'name': 'Lake Road',
        'color': '#00857C',
        'allocated': 30,
        'seat_capacity': 32,
        'pct': 94,
        'level': 'warning',
        'active_schedules': 2,
      },
      {
        'route_id': 3,
        'code': '22',
        'name': 'Hill View',
        'color': '#8C1D40',
        'allocated': 8,
        'seat_capacity': 50,
        'pct': 16,
        'level': 'ok',
        'active_schedules': 2,
      },
    ],
  };
}

/// `/trips/7/live`: the bus between stops 2 and 3; the campus has no coordinates.
Json liveTripJson() => {
  'trip_id': 7,
  'route': route14,
  'direction': 'pickup',
  'status': 'in_progress',
  'bus_registration_no': 'TN09AB1401',
  'delay_min': 8,
  'next_stop_sequence': 3,
  'position': {
    'trip_id': 7,
    'bus_id': 1,
    'latitude': 12.95,
    'longitude': 80.237,
    'speed_kmph': 28,
    'heading_deg': 190,
    'accuracy_m': 8,
    'recorded_at': at(0),
  },
  'stops': [
    for (var i = 0; i < _stopNames.length; i++)
      {
        'sequence': i + 1,
        'stop_id': i + 1,
        'name': _stopNames[i],
        'latitude': i == _stopNames.length - 1 ? null : 12.99 - 0.02 * i,
        'longitude': i == _stopNames.length - 1 ? null : 80.248 - 0.005 * i,
        'scheduled_at': at(-20 + _offsets[i]),
        'arrived_at': i < 2 ? at(-20 + _offsets[i]) : null,
      },
  ],
};

/// A report as the admin sees it: analysed, with one confirmed finding and a found-item match.
Json reportJson({String status = 'open', bool anonymous = false, bool analysed = true}) => {
  'id': 5,
  'kind': 'lost_item',
  'description': 'Left my blue water bottle on the bus',
  'status': status,
  'anonymous': anonymous,
  'trip_id': 7,
  'service_date': '2026-10-02',
  'direction': 'pickup',
  'route_code': '14',
  'route_color': '#0B5CAD',
  'stop_name': 'Thirumangalam',
  'created_at': at(-30),
  'updated_at': at(-29),
  'resolution_note': null,
  'messages': [
    {'id': 1, 'from_staff': true, 'body': 'We found it.', 'created_at': at(-5)},
  ],
  'student_id': anonymous ? null : 12,
  'student_name': anonymous ? null : 'Priya R',
  'roll_no': anonymous ? null : '22CSE012',
  'bus_registration_no': 'TN09AB1401',
  'severity': 'low',
  'analysis_status': analysed ? 'done' : 'pending',
  'analysed_by': analysed ? 'qwen3:4b' : null,
  'analysed_at': analysed ? at(-29) : null,
  'matched_found_item_id': null,
  'closed_at': null,
  'analysis': analysed
      ? {
          'claims': {'subtype': 'lost_item'},
          'findings': [
            {'check': 'on_this_trip', 'verdict': 'confirmed', 'detail': 'The student boarded at 07:41.', 'numbers': {}},
            {'check': 'lost_item', 'verdict': 'partly', 'detail': 'A possible match was logged.', 'numbers': {}},
          ],
          'match_candidates': [
            {
              'found_item_id': 3,
              'description': 'Blue steel water bottle',
              'score': 0.75,
              'logged_at': at(-20),
              'bus_registration_no': 'TN09AB1401',
            },
          ],
          'summary': 'Student lost a bottle; a likely match was logged.',
          'suggested_action': 'Check found items.',
          'draft_reply': 'Thanks, we may have it.',
          'steps': {'read': 'qwen3:4b', 'write': 'qwen3:4b'},
        }
      : null,
};
