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
  String _csrfToken = '';

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
    return _dio.post(
      path,
      data: data,
      options: _csrfOptions(),
    );
  }

  Future<Response<dynamic>> delete(String path,
      {Map<String, dynamic>? data}) {
    return _dio.delete(
      path,
      data: data,
      options: _csrfOptions(),
    );
  }

  Options? _csrfOptions() {
    if (_csrfToken.isEmpty) return null;
    return Options(headers: {'X-CSRF-Token': _csrfToken});
  }

  void _captureCsrfToken(Map<String, dynamic> data) {
    final token = data['csrf_token']?.toString() ?? '';
    if (token.isNotEmpty) {
      _csrfToken = token;
    }
  }

  Future<Map<String, dynamic>> authConfig() async {
    final response = await get('/api/mobile/auth_config');
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> register({
    required String username,
    required String email,
    required String password,
    required String confirmPassword,
    String inviteCode = '',
    String turnstileToken = '',
  }) async {
    final response = await post('/api/mobile/register', data: {
      'username': username,
      'email': email,
      'password': password,
      'confirm_password': confirmPassword,
      'invite_code': inviteCode,
      'turnstile_token': turnstileToken,
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> login({
    required String usernameOrEmail,
    required String password,
    String turnstileToken = '',
  }) async {
    final response = await post('/api/mobile/login', data: {
      'username': usernameOrEmail,
      'password': password,
      'turnstile_token': turnstileToken,
    });
    final data = _ensureJsonMap(response.data);
    _captureCsrfToken(data);
    return data;
  }

  Future<Map<String, dynamic>> githubAuthUrl() async {
    final response = await get('/api/mobile/github/auth_url');
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> completeGithubLogin({
    required String token,
  }) async {
    final response = await post('/api/mobile/github/complete', data: {
      'token': token,
    });
    final data = _ensureJsonMap(response.data);
    _captureCsrfToken(data);
    return data;
  }

  Future<Map<String, dynamic>> forgotPassword({
    required String email,
    String turnstileToken = '',
  }) async {
    final response = await post('/api/mobile/forgot_password', data: {
      'email': email,
      'turnstile_token': turnstileToken,
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> logout() async {
    final response = await post('/api/mobile/logout');
    _csrfToken = '';
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

  Future<Map<String, dynamic>> activationRequests() async {
    final response = await get('/api/mobile/activation_requests');
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> requestActivationCode({String? note}) async {
    final response = await post('/api/mobile/activation_requests', data: {
      'request_note': note ?? '',
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> me() async {
    final response = await get('/api/mobile/me');
    final data = _ensureJsonMap(response.data);
    _captureCsrfToken(data);
    return data;
  }

  Future<Map<String, dynamic>> updatePredictionDisplaySettings({
    required bool showNormalNumbers,
  }) async {
    final response = await post('/api/mobile/settings/prediction-display', data: {
      'show_normal_numbers': showNormalNumbers,
    });
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
    late final Response<ResponseBody> response;
    try {
      response = await _dio.get<ResponseBody>(
        '/api/predict',
        queryParameters: {
          'region': region,
          'strategy': 'ai',
          'year': year,
          'stream': '1',
        },
        options: Options(
          responseType: ResponseType.stream,
          receiveTimeout: const Duration(minutes: 2),
          validateStatus: (status) => status != null && status < 600,
          headers: {
            'Accept': 'text/event-stream',
            'Cache-Control': 'no-cache',
          },
        ),
      );
    } on DioException catch (e) {
      yield {'type': 'error', 'error': _formatDioError(e)};
      return;
    } catch (_) {
      yield {'type': 'error', 'error': 'AI预测服务连接失败，请稍后重试'};
      return;
    }

    if (response.statusCode != 200) {
      final bytes = await response.data?.stream
              .fold<List<int>>(<int>[], (acc, chunk) => acc..addAll(chunk)) ??
          <int>[];
      final body = bytes.isEmpty ? '' : utf8.decode(bytes);
      try {
        final data = jsonDecode(body);
        if (data is Map<String, dynamic>) {
          yield {
            'type': 'error',
            'error': data['message'] ??
                data['error'] ??
                _formatHttpError(response.statusCode),
          };
          return;
        }
      } catch (_) {}
      yield {'type': 'error', 'error': _formatHttpError(response.statusCode)};
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
      final payload = _extractSsePayload(trimmed);
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
    bool includeZodiacs = false,
    bool includeSummaries = true,
    bool includeDetails = true,
    bool includeTotal = true,
    String? year,
  }) async {
    final response = await get('/api/mobile/predictions', queryParameters: {
      'page': page.toString(),
      'page_size': pageSize.toString(),
      if (region != null && region.isNotEmpty) 'region': region,
      if (strategy != null && strategy.isNotEmpty) 'strategy': strategy,
      if (result != null && result.isNotEmpty) 'result': result,
      if (includeZodiacs) 'include_zodiacs': '1',
      if (!includeSummaries) 'include_summaries': '0',
      if (!includeDetails) 'include_details': '0',
      if (!includeTotal) 'include_total': '0',
      if (year != null && year.isNotEmpty) 'year': year,
    });
    return _ensureJsonMap(response.data);
  }

  Future<Map<String, dynamic>> predictionSummaries({String? region}) async {
    final response = await get('/api/mobile/prediction_summaries',
        queryParameters: {
          if (region != null && region.isNotEmpty) 'region': region,
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

  Future<Map<String, dynamic>> nextDrawTime({
    required String region,
  }) async {
    final response = await get('/api/next_draw_time', queryParameters: {
      'region': region,
    });
    return _ensureJsonMap(response.data);
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

  Future<Map<String, dynamic>> deleteManualBet({required int id}) async {
    final response = await delete('/api/mobile/manual_bets/$id');
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
    List<Map<String, dynamic>>? numberStakes,
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
      if (numberStakes != null) 'number_stakes': numberStakes,
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

  String _formatDioError(DioException error) {
    final statusCode = error.response?.statusCode;
    if (statusCode != null) {
      return _formatHttpError(statusCode);
    }
    switch (error.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.sendTimeout:
      case DioExceptionType.receiveTimeout:
        return 'AI预测服务响应超时，请稍后重试';
      case DioExceptionType.connectionError:
        return 'AI预测服务连接失败，请检查网络后重试';
      default:
        return 'AI预测服务暂时不可用，请稍后重试';
    }
  }

  String _formatHttpError(int? statusCode) {
    if (statusCode == null) {
      return 'AI预测服务暂时不可用，请稍后重试';
    }
    if (statusCode == 401 || statusCode == 403) {
      return '登录状态或权限已失效，请重新登录后再试';
    }
    if (statusCode == 429) {
      return 'AI预测请求过于频繁，请稍后再试';
    }
    if (statusCode >= 500) {
      return 'AI预测服务暂时不可用（HTTP $statusCode），请稍后重试';
    }
    return 'AI预测请求失败（HTTP $statusCode），请稍后重试';
  }

  Stream<String> _splitByDoubleNewline(Stream<String> source) async* {
    var buffer = '';
    await for (final chunk in source) {
      buffer += chunk;
      while (true) {
        var delimiterLength = 2;
        var index = buffer.indexOf('\r\n\r\n');
        if (index != -1) {
          delimiterLength = 4;
        } else {
          index = buffer.indexOf('\n\n');
        }
        if (index == -1) break;
        final part = buffer.substring(0, index);
        buffer = buffer.substring(index + delimiterLength);
        if (part.isNotEmpty) {
          yield part;
        }
      }
    }
    if (buffer.trim().isNotEmpty) {
      yield buffer;
    }
  }

  String _extractSsePayload(String chunk) {
    if (!chunk.contains('data:')) {
      return chunk;
    }
    final payloadLines = <String>[];
    for (final line in chunk.split(RegExp(r'\r?\n'))) {
      if (line.startsWith('data:')) {
        payloadLines.add(line.substring(5).trimLeft());
      }
    }
    return payloadLines.join('\n').trim();
  }
}
