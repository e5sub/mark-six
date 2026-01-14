class UserProfile {
  UserProfile({
    required this.id,
    required this.username,
    required this.email,
    required this.isActive,
    this.activationExpiresAt,
  });

  final int id;
  final String username;
  final String email;
  final bool isActive;
  final DateTime? activationExpiresAt;

  factory UserProfile.fromJson(Map<String, dynamic> json) {
    return UserProfile(
      id: json['id'] as int,
      username: json['username'] as String? ?? '',
      email: json['email'] as String? ?? '',
      isActive: json['is_active'] as bool? ?? false,
      activationExpiresAt: json['activation_expires_at'] == null
          ? null
          : DateTime.tryParse(json['activation_expires_at'] as String),
    );
  }
}

class PredictionItem {
  PredictionItem({
    required this.id,
    required this.region,
    required this.strategy,
    required this.period,
    required this.normalNumbers,
    required this.normalZodiacs,
    required this.specialNumber,
    required this.specialZodiac,
    required this.actualSpecialNumber,
    required this.actualSpecialZodiac,
    required this.result,
    required this.createdAt,
  });

  final int id;
  final String region;
  final String strategy;
  final String period;
  final List<String> normalNumbers;
  final List<String> normalZodiacs;
  final String specialNumber;
  final String specialZodiac;
  final String actualSpecialNumber;
  final String actualSpecialZodiac;
  final String result;
  final DateTime? createdAt;

  factory PredictionItem.fromJson(Map<String, dynamic> json) {
    return PredictionItem(
      id: json['id'] as int,
      region: json['region'] as String? ?? '',
      strategy: json['strategy'] as String? ?? '',
      period: json['period'] as String? ?? '',
      normalNumbers: (json['normal_numbers'] as List<dynamic>? ?? [])
          .map((value) => value.toString())
          .toList(),
      normalZodiacs: (json['normal_zodiacs'] as List<dynamic>? ?? [])
          .map((value) => value.toString())
          .toList(),
      specialNumber: json['special_number'] as String? ?? '',
      specialZodiac: json['special_zodiac'] as String? ?? '',
      actualSpecialNumber: json['actual_special_number'] as String? ?? '',
      actualSpecialZodiac: json['actual_special_zodiac'] as String? ?? '',
      result: json['result'] as String? ?? 'pending',
      createdAt: json['created_at'] == null
          ? null
          : DateTime.tryParse(json['created_at'] as String),
    );
  }
}

class AccuracyStats {
  AccuracyStats({
    required this.total,
    required this.specialHits,
    required this.normalHits,
    required this.correct,
    required this.accuracy,
  });

  final int total;
  final int specialHits;
  final int normalHits;
  final int correct;
  final double accuracy;

  factory AccuracyStats.fromJson(Map<String, dynamic> json) {
    return AccuracyStats(
      total: (json['total'] as num?)?.toInt() ?? 0,
      specialHits: (json['special_hits'] as num?)?.toInt() ?? 0,
      normalHits: (json['normal_hits'] as num?)?.toInt() ?? 0,
      correct: (json['correct'] as num?)?.toInt() ?? 0,
      accuracy: (json['accuracy'] as num?)?.toDouble() ?? 0.0,
    );
  }
}

class DrawRecord {
  DrawRecord({
    required this.id,
    required this.date,
    required this.normalNumbers,
    required this.specialNumber,
    required this.specialZodiac,
    required this.rawZodiacs,
  });

  final String id;
  final String date;
  final List<String> normalNumbers;
  final String specialNumber;
  final String specialZodiac;
  final List<String> rawZodiacs;

  factory DrawRecord.fromJson(Map<String, dynamic> json) {
    final rawZodiac = json['raw_zodiac']?.toString() ?? '';
    final rawZodiacs = rawZodiac.isEmpty
        ? <String>[]
        : rawZodiac.split(',').map((value) => value.trim()).toList();
    return DrawRecord(
      id: json['id']?.toString() ?? '',
      date: json['date']?.toString() ?? '',
      normalNumbers: (json['no'] as List<dynamic>? ?? [])
          .map((value) => value.toString())
          .toList(),
      specialNumber: json['sno']?.toString() ?? '',
      specialZodiac: json['sno_zodiac']?.toString() ?? '',
      rawZodiacs: rawZodiacs,
    );
  }
}
