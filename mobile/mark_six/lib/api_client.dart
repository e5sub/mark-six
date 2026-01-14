import 'dart:async';
import 'dart:convert';

import 'package:cookie_jar/cookie_jar.dart';
import 'package:dio/dio.dart';
import 'package:dio_cookie_manager/dio_cookie_manager.dart';
import 'package:path_provider/path_provider.dart';

import 'config.dart';

class ApiClient {
  ApiClient._();

  static final ApiClient instance = ApiClient._();

  late final Dio _dio;
  bool _initialized = false;

  Future<void> init() async {
    if (_initialized) return;
    final options = BaseOptions(
      baseUrl: baseUrl,
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 30),
      headers: {'Content-Type': 'application/json'},
      validateStatus: (status) => status != null && status < 500,
    );
    _dio = Dio(options);

    final dir = await getApplicationDocumentsDirectory();
    final cookieJar = PersistCookieJar(storage: FileStorage(dir.path));
    _dio.interceptors.add(CookieManager(cookieJar));

    _initialized = true;
  }

  Future<Response<dynamic>> get(String path,
      {Map<String, dynamic>? queryParameters}) {
    return _dio.get(path, queryParameters: queryParameters);
  }

  Future<Response<dynamic>> post(String path, {Map<String, dynamic>? data}) {
    return _dio.post(path, data: data);
  }

  Future<Map<String, dynamic>> register({
    required String username,
    required String email,
    required String password,
    required String confirmPassword,
    String inviteCode = '',
  }) async {
    final response = await post('/api/mobile/register', data: {
      'username': username,
      'email': email,
      'password': password,
      'confirm_password': confirmPassword,
      'invite_code': inviteCode,
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> login({
    required String usernameOrEmail,
    required String password,
  }) async {
    final response = await post('/api/mobile/login', data: {
      'username': usernameOrEmail,
      'password': password,
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> logout() async {
    final response = await post('/api/mobile/logout');
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> changePassword({
    required String currentPassword,
    required String newPassword,
    required String confirmPassword,
  }) async {
    final response = await post('/api/mobile/change_password', data: {
      'current_password': currentPassword,
      'new_password': newPassword,
      'confirm_password': confirmPassword,
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> activate({required String code}) async {
    final response = await post('/api/mobile/activate', data: {
      'activation_code': code,
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> me() async {
    final response = await get('/api/mobile/me');
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> predict({
    required String region,
    required String strategy,
    required String year,
  }) async {
    final response = await get('/api/predict', queryParameters: {
      'region': region,
      'strategy': strategy,
      'year': year,
      if (strategy == 'ai') 'stream': '0',
    });
    return _ensureJsonMap(response.data);
  }

  Stream<Map<String, dynamic>> predictAiStream({
    required String region,
    required String year,
  }) async* {
    final response = await _dio.get<ResponseBody>(
      '/api/predict',
      queryParameters: {
        'region': region,
        'strategy': 'ai',
        'year': year,
        'stream': '1',
      },
      options: Options(responseType: ResponseType.stream),
    );

    if (response.statusCode != 200) {
      final bytes = await response.data?.stream
              .fold<List<int>>(<int>[], (acc, chunk) => acc..addAll(chunk)) ??
          <int>[];
      final body = bytes.isEmpty ? '' : utf8.decode(bytes);
      try {
        final data = jsonDecode(body);
        if (data is Map<String, dynamic>) {
          yield {'type': 'error', 'error': data['message'] ?? data['error'] ?? body};
          return;
        }
      } catch (_) {}
      yield {'type': 'error', 'error': body.isEmpty ? 'AI预测失败' : body};
      return;
    }

    final stream = response.data?.stream;
    if (stream == null) {
      yield {'type': 'error', 'error': 'empty stream'};
      return;
    }

    final textStream = stream.cast<List<int>>().transform(utf8.decoder);
    await for (final chunk in _splitByDoubleNewline(textStream)) {
      final trimmed = chunk.trim();
      if (trimmed.isEmpty) continue;
      final payload =
          trimmed.startsWith('data:') ? trimmed.substring(5).trim() : trimmed;
      if (payload.isEmpty) continue;
      try {
        yield _ensureJsonMap(jsonDecode(payload) as Map<String, dynamic>);
      } catch (_) {
        yield {'type': 'content', 'content': payload};
      }
    }
  }

  Future<Map<String, dynamic>> predictions({
    int page = 1,
    int pageSize = 20,
    String? region,
    String? strategy,
    String? result,
  }) async {
    final response = await get('/api/mobile/predictions', queryParameters: {
      'page': page.toString(),
      'page_size': pageSize.toString(),
      if (region != null && region.isNotEmpty) 'region': region,
      if (strategy != null && strategy.isNotEmpty) 'strategy': strategy,
      if (result != null && result.isNotEmpty) 'result': result,
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> accuracy() async {
    final response = await get('/api/mobile/accuracy');
    return _ensureJsonMap(response.data);
  }

  Future<List<dynamic>> draws({
    required String region,
    required String year,
  }) async {
    final response = await get('/api/draws', queryParameters: {
      'region': region,
      'year': year,
    });
    if (response.data is List) {
      return response.data as List<dynamic>;
    }
    if (response.data is String && (response.data as String).isNotEmpty) {
      final decoded = jsonDecode(response.data as String);
      if (decoded is List) {
        return decoded;
      }
    }
    return <dynamic>[];
  }

  Future<Map<String, dynamic>> getZodiacs({
    required List<String> numbers,
    required String region,
    required String year,
  }) async {
    final response = await get('/api/get_zodiacs', queryParameters: {
      'numbers': numbers.join(','),
      'region': region,
      'year': year,
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> createManualBet({
    required String region,
    required String period,
    bool settle = true,
    int? recordId,
    String bettorName = '',
    required bool betNumber,
    required bool betZodiac,
    required bool betColor,
    required bool betParity,
    required List<int> numbers,
    required List<String> zodiacs,
    required List<String> colors,
    required List<String> parity,
    required String stakeSpecial,
    required String stakeCommon,
    required String oddsNumber,
    required String oddsZodiac,
    required String oddsColor,
    required String oddsParity,
  }) async {
    final response = await post('/api/mobile/manual_bets', data: {
      'region': region,
      'period': period,
      'settle': settle,
      if (recordId != null) 'record_id': recordId,
      if (bettorName.trim().isNotEmpty) 'bettor_name': bettorName.trim(),
      'bet_number': betNumber,
      'bet_zodiac': betZodiac,
      'bet_color': betColor,
      'bet_parity': betParity,
      'numbers': numbers,
      'zodiacs': zodiacs,
      'colors': colors,
      'parity': parity,
      'stake_special': stakeSpecial,
      'stake_common': stakeCommon,
      'odds_number': oddsNumber,
      'odds_zodiac': oddsZodiac,
      'odds_color': oddsColor,
      'odds_parity': oddsParity,
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> manualBets({
    String? region,
    String? status,
    int limit = 20,
  }) async {
    final response = await get('/api/mobile/manual_bets', queryParameters: {
      if (region != null && region.isNotEmpty) 'region': region,
      if (status != null && status.isNotEmpty) 'status': status,
      'limit': limit.toString(),
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> manualBetSummary({String? region}) async {
    final response =
        await get('/api/mobile/manual_bets/summary', queryParameters: {
      if (region != null && region.isNotEmpty) 'region': region,
    });
    return _ensureJsonMap(response.data);
  }

  Map<String, dynamic> _ensureJsonMap(dynamic data) {
    if (data is Map<String, dynamic>) {
      return data;
    }
    if (data is String && data.isNotEmpty) {
      return jsonDecode(data) as Map<String, dynamic>;
    }
    return <String, dynamic>{};
  }

  Stream<String> _splitByDoubleNewline(Stream<String> source) async* {
    var buffer = '';
    await for (final chunk in source) {
      buffer += chunk;
      while (true) {
        final index = buffer.indexOf('\n\n');
        if (index == -1) break;
        final part = buffer.substring(0, index);
        buffer = buffer.substring(index + 2);
        if (part.isNotEmpty) {
          yield part;
        }
      }
    }
    if (buffer.trim().isNotEmpty) {
      yield buffer;
    }
  }
}
