
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:dio/dio.dart';
import 'package:open_filex/open_filex.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:path_provider/path_provider.dart';
import 'package:webview_flutter/webview_flutter.dart';

import 'api_client.dart';
import 'config.dart';
import 'models.dart';

const String _lastRegionPrefKey = 'last_selected_region';

String _normalizeRegion(String? value) {
  return value == 'hk' ? 'hk' : 'macau';
}

Future<String> _loadLastRegion() async {
  final prefs = await SharedPreferences.getInstance();
  return _normalizeRegion(prefs.getString(_lastRegionPrefKey));
}

Future<void> _saveLastRegion(String value) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.setString(_lastRegionPrefKey, _normalizeRegion(value));
}

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const MarkSixApp());
}

class MarkSixApp extends StatefulWidget {
  const MarkSixApp({super.key});

  @override
  State<MarkSixApp> createState() => _MarkSixAppState();
}

class _MarkSixAppState extends State<MarkSixApp> {
  final AppState _appState = AppState();
  bool _updateChecked = false;

  @override
  void initState() {
    super.initState();
    _appState.init();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '彩研所',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFFB91C1C),
          secondary: const Color(0xFFF4B547),
        ),
        fontFamily: 'NotoSansSC',
        textTheme: ThemeData.light().textTheme.apply(fontFamily: 'NotoSansSC'),
        primaryTextTheme:
            ThemeData.light().textTheme.apply(fontFamily: 'NotoSansSC'),
        useMaterial3: true,
        scaffoldBackgroundColor: const Color(0xFFF6F7FB),
        appBarTheme: const AppBarTheme(
          backgroundColor: Color(0xFFF6F7FB),
          elevation: 0,
          centerTitle: true,
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: Colors.white,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(16),
            borderSide: BorderSide.none,
          ),
        ),
      ),
      home: AnimatedBuilder(
        animation: _appState,
        builder: (context, _) {
          if (!_appState.initialized) {
            return const SplashScreen();
          }
          if (!_updateChecked) {
            _updateChecked = true;
            WidgetsBinding.instance.addPostFrameCallback((_) {
              UpdateService.checkForUpdate(context);
            });
          }
          if (_appState.user == null) {
            return LoginScreen(appState: _appState);
          }
          return HomeScreen(appState: _appState);
        },
      ),
    );
  }
}

class AppState extends ChangeNotifier {
  UserProfile? user;
  bool initialized = false;
  String turnstileSiteKey = '';
  bool githubLoginEnabled = false;

  bool get activationValid {
    final current = user;
    if (current == null) return false;
    if (!current.isActive) return false;
    final expiresAt = current.activationExpiresAt;
    if (expiresAt == null) return true;
    return !expiresAt.isBefore(DateTime.now());
  }

  Future<void> init() async {
    await ApiClient.instance.init();
    await loadAuthConfig();
    await loadMe();
    initialized = true;
    notifyListeners();
  }

  Future<void> loadAuthConfig() async {
    try {
      final res = await ApiClient.instance.authConfig();
      if (res['success'] == true) {
        turnstileSiteKey = res['turnstile_site_key']?.toString() ?? '';
        githubLoginEnabled = res['github_login_enabled'] == true;
      }
    } catch (_) {
      turnstileSiteKey = '';
      githubLoginEnabled = false;
    }
  }

  Future<void> loadMe() async {
    try {
      final res = await ApiClient.instance.me();
      if (res['success'] == true) {
        user = UserProfile.fromJson(res['user'] as Map<String, dynamic>);
      } else {
        user = null;
      }
    } catch (_) {
      user = null;
    }
  }

  Future<String?> login(
    String usernameOrEmail,
    String password, {
    String turnstileToken = '',
  }) async {
    try {
      final res = await ApiClient.instance.login(
        usernameOrEmail: usernameOrEmail,
        password: password,
        turnstileToken: turnstileToken,
      );
      if (res['success'] == true) {
        user = UserProfile.fromJson(res['user'] as Map<String, dynamic>);
        notifyListeners();
        return null;
      }
      return res['message']?.toString() ?? '登录失败';
    } catch (e) {
      return '登录失败: $e';
    }
  }

  Future<String?> register({
    required String username,
    required String email,
    required String password,
    required String confirmPassword,
    String inviteCode = '',
    String turnstileToken = '',
  }) async {
    try {
      final res = await ApiClient.instance.register(
        username: username,
        email: email,
        password: password,
        confirmPassword: confirmPassword,
        inviteCode: inviteCode,
        turnstileToken: turnstileToken,
      );
      if (res['success'] == true) {
        return null;
      }
      return res['message']?.toString() ?? '注册失败';
    } catch (e) {
      return '注册失败: $e';
    }
  }

  Future<String?> completeGithubLogin(String token) async {
    try {
      final res = await ApiClient.instance.completeGithubLogin(token: token);
      if (res['success'] == true) {
        user = UserProfile.fromJson(res['user'] as Map<String, dynamic>);
        notifyListeners();
        return null;
      }
      return res['message']?.toString() ?? 'GitHub 登录失败';
    } catch (e) {
      return 'GitHub 登录失败: $e';
    }
  }

  Future<String?> activate(String code) async {
    try {
      final res = await ApiClient.instance.activate(code: code);
      if (res['success'] == true) {
        await loadMe();
        notifyListeners();
        return null;
      }
      return res['message']?.toString() ?? '激活失败';
    } catch (e) {
      return '激活失败: $e';
    }
  }

  Future<void> logout() async {
    try {
      await ApiClient.instance.logout();
    } finally {
      user = null;
      notifyListeners();
    }
  }

  Future<String?> changePassword({
    required String currentPassword,
    required String newPassword,
    required String confirmPassword,
  }) async {
    try {
      final res = await ApiClient.instance.changePassword(
        currentPassword: currentPassword,
        newPassword: newPassword,
        confirmPassword: confirmPassword,
      );
      if (res['success'] == true) {
        return null;
      }
      return res['message']?.toString() ?? '修改密码失败';
    } catch (e) {
      return '修改密码失败: $e';
    }
  }

  Future<String?> updateShowNormalNumbers(bool value) async {
    try {
      final res = await ApiClient.instance.updatePredictionDisplaySettings(
        showNormalNumbers: value,
      );
      if (res['success'] == true) {
        user = UserProfile.fromJson(res['user'] as Map<String, dynamic>);
        notifyListeners();
        return null;
      }
      return res['message']?.toString() ?? '保存设置失败';
    } catch (e) {
      return '保存设置失败: $e';
    }
  }
}

Future<void> showActivationDialog(
  BuildContext context,
  AppState appState,
) async {
  String statusLabel(String value) {
    switch (value) {
      case 'pending':
        return '待处理';
      case 'issued':
        return '已发放';
      case 'used':
        return '已使用';
      case 'rejected':
        return '已驳回';
      default:
        return value;
    }
  }

  final controller = TextEditingController();
  final noteController = TextEditingController();
  List<Map<String, dynamic>> requestItems = [];
  bool requestSubmitting = false;

  try {
    final res = await ApiClient.instance.activationRequests();
    requestItems = ((res['requests'] as List?) ?? const [])
        .whereType<Map>()
        .map((item) => Map<String, dynamic>.from(item))
        .toList();
  } catch (_) {
    requestItems = [];
  }

  bool hasPendingRequest() => requestItems.any(
        (item) => (item['status']?.toString() ?? '') == 'pending',
      );

  String? result;
  try {
    result = await showDialog<String>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, setState) => AlertDialog(
          title: const Text('激活账号'),
          content: SizedBox(
            width: 420,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  TextField(
                    controller: controller,
                    decoration: const InputDecoration(labelText: '激活码'),
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    '没有激活码？可以直接申请，管理员发放后会显示在下方。',
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: noteController,
                    minLines: 2,
                    maxLines: 3,
                    decoration: const InputDecoration(
                      labelText: '申请说明（可选）',
                      hintText: '例如：需要永久码或 1 个月试用码',
                    ),
                  ),
                  const SizedBox(height: 10),
                  FilledButton.tonal(
                    onPressed: requestSubmitting || hasPendingRequest()
                        ? null
                        : () async {
                            setState(() => requestSubmitting = true);
                            try {
                              final res = await ApiClient.instance
                                  .requestActivationCode(
                                note: noteController.text.trim(),
                              );
                              final rows = ((res['requests'] as List?) ?? const [])
                                  .whereType<Map>()
                                  .map((item) => Map<String, dynamic>.from(item))
                                  .toList();
                              if (rows.isNotEmpty) {
                                requestItems = rows;
                              } else if (res['request'] is Map) {
                                requestItems = [
                                  Map<String, dynamic>.from(
                                    res['request'] as Map,
                                  ),
                                  ...requestItems,
                                ];
                              }
                              noteController.clear();
                              if (context.mounted) {
                                ScaffoldMessenger.of(context).showSnackBar(
                                  SnackBar(
                                    content: Text(
                                      res['message']?.toString() ?? '申请已提交',
                                    ),
                                  ),
                                );
                              }
                            } catch (e) {
                              if (context.mounted) {
                                ScaffoldMessenger.of(context).showSnackBar(
                                  SnackBar(content: Text('申请失败: $e')),
                                );
                              }
                            } finally {
                              if (dialogContext.mounted) {
                                setState(() => requestSubmitting = false);
                              }
                            }
                          },
                    child: Text(requestSubmitting ? '提交中...' : '申请获取激活码'),
                  ),
                  if (hasPendingRequest()) ...[
                    const SizedBox(height: 8),
                    const Text(
                      '当前已有一条申请中的记录，请等待管理员处理。',
                      style: TextStyle(
                        color: Color(0xFFB45309),
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                  if (requestItems.isNotEmpty) ...[
                    const SizedBox(height: 16),
                    const Divider(),
                    const SizedBox(height: 8),
                    const Text(
                      '最近申请记录',
                      style: TextStyle(fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 8),
                    ...requestItems.take(5).map(
                      (item) => Container(
                        width: double.infinity,
                        margin: const EdgeInsets.only(bottom: 8),
                        padding: const EdgeInsets.all(10),
                        decoration: BoxDecoration(
                          color: const Color(0xFFF3F6FB),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              statusLabel(item['status']?.toString() ?? ''),
                              style: const TextStyle(
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                            if ((item['issued_code']?.toString() ?? '').isNotEmpty)
                              Padding(
                                padding: const EdgeInsets.only(top: 4),
                                child: Text(
                                  '激活码：${item['issued_code']}',
                                  style: const TextStyle(
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ),
                            if ((item['issued_validity_label']?.toString() ?? '')
                                .isNotEmpty)
                              Padding(
                                padding: const EdgeInsets.only(top: 2),
                                child: Text(
                                  '类型：${item['issued_validity_label']}',
                                ),
                              ),
                            if ((item['admin_note']?.toString() ?? '').isNotEmpty)
                              Padding(
                                padding: const EdgeInsets.only(top: 2),
                                child: Text(
                                  '备注：${item['admin_note']}',
                                ),
                              ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(),
              child: const Text('取消'),
            ),
            TextButton(
              onPressed: () =>
                  Navigator.of(dialogContext).pop(controller.text.trim()),
              child: const Text('激活'),
            ),
          ],
        ),
      ),
    );
  } finally {
    controller.dispose();
    noteController.dispose();
  }

  if (result == null || result.isEmpty) return;
  final error = await appState.activate(result);
  if (!context.mounted) return;
  if (error != null) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(error)),
    );
  }
}

class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(
        child: CircularProgressIndicator(),
      ),
    );
  }
}

class TurnstileBox extends StatefulWidget {
  const TurnstileBox({
    super.key,
    required this.siteKey,
    required this.onTokenChanged,
  });

  final String siteKey;
  final ValueChanged<String> onTokenChanged;

  @override
  State<TurnstileBox> createState() => _TurnstileBoxState();
}

class _TurnstileBoxState extends State<TurnstileBox> {
  late final WebViewController _controller;

  @override
  void initState() {
    super.initState();
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..addJavaScriptChannel(
        'TurnstileToken',
        onMessageReceived: (message) {
          widget.onTokenChanged(message.message);
        },
      )
      ..loadHtmlString(_turnstileHtml(widget.siteKey), baseUrl: baseUrl);
  }

  @override
  void didUpdateWidget(TurnstileBox oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.siteKey != widget.siteKey) {
      _controller.loadHtmlString(_turnstileHtml(widget.siteKey), baseUrl: baseUrl);
    }
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 78,
      width: double.infinity,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(12),
        child: WebViewWidget(controller: _controller),
      ),
    );
  }

  String _turnstileHtml(String siteKey) {
    return '''
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
  <style>
    html, body {
      margin: 0;
      padding: 0;
      background: transparent;
      overflow: hidden;
    }
    .wrap {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 76px;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="cf-turnstile"
      data-sitekey="$siteKey"
      data-callback="onTurnstileToken"
      data-expired-callback="onTurnstileExpired"
      data-error-callback="onTurnstileExpired"></div>
  </div>
  <script>
    function onTurnstileToken(token) {
      TurnstileToken.postMessage(token || '');
    }
    function onTurnstileExpired() {
      TurnstileToken.postMessage('');
    }
  </script>
</body>
</html>
''';
  }
}

class GithubLoginDialog extends StatefulWidget {
  const GithubLoginDialog({super.key, required this.authUrl});

  final String authUrl;

  @override
  State<GithubLoginDialog> createState() => _GithubLoginDialogState();
}

class _GithubLoginDialogState extends State<GithubLoginDialog> {
  late final WebViewController _controller;

  @override
  void initState() {
    super.initState();
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setNavigationDelegate(
        NavigationDelegate(
          onNavigationRequest: (request) {
            final uri = Uri.tryParse(request.url);
            if (uri != null &&
                uri.path == '/api/mobile/github/success' &&
                uri.queryParameters['token'] != null) {
              Navigator.of(context).pop(uri.queryParameters['token']);
              return NavigationDecision.prevent;
            }
            return NavigationDecision.navigate;
          },
        ),
      )
      ..loadRequest(Uri.parse(widget.authUrl));
  }

  @override
  Widget build(BuildContext context) {
    return Dialog.fullscreen(
      child: Scaffold(
        appBar: AppBar(
          title: const Text('GitHub 登录'),
          leading: IconButton(
            icon: const Icon(Icons.close),
            onPressed: () => Navigator.of(context).pop(),
          ),
        ),
        body: WebViewWidget(controller: _controller),
      ),
    );
  }
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.appState});

  final AppState appState;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final TextEditingController _username = TextEditingController();
  final TextEditingController _password = TextEditingController();
  bool _loading = false;
  bool _githubLoading = false;
  String _turnstileToken = '';
  int _turnstileReset = 0;

  @override
  void dispose() {
    _username.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _handleLogin() async {
    if (widget.appState.turnstileSiteKey.isNotEmpty && _turnstileToken.isEmpty) {
      _showMessage('请先完成人机验证');
      return;
    }
    setState(() => _loading = true);
    final error = await widget.appState.login(
      _username.text.trim(),
      _password.text,
      turnstileToken: _turnstileToken,
    );
    setState(() => _loading = false);
    if (error != null && mounted) {
      setState(() {
        _turnstileToken = '';
        _turnstileReset++;
      });
      _showMessage(error);
    }
  }

  Future<void> _handleGithubLogin() async {
    setState(() => _githubLoading = true);
    try {
      final res = await ApiClient.instance.githubAuthUrl();
      if (res['success'] != true) {
        _showMessage(res['message']?.toString() ?? 'GitHub 登录未配置');
        return;
      }
      final authUrl = res['auth_url']?.toString() ?? '';
      if (authUrl.isEmpty) {
        _showMessage('GitHub 授权地址无效');
        return;
      }
      final token = await showDialog<String>(
        context: context,
        builder: (_) => GithubLoginDialog(authUrl: authUrl),
      );
      if (token == null || token.isEmpty) return;
      final error = await widget.appState.completeGithubLogin(token);
      if (error != null && mounted) {
        _showMessage(error);
      }
    } catch (e) {
      if (mounted) {
        _showMessage('GitHub 登录失败: $e');
      }
    } finally {
      if (mounted) {
        setState(() => _githubLoading = false);
      }
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            colors: [Color(0xFFB91C1C), Color(0xFFE11D48)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
        ),
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Card(
              elevation: 6,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(24),
              ),
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      '彩研所',
                      style:
                          TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      '登录后开始你的智能预测',
                      style: TextStyle(color: Colors.grey.shade600),
                    ),
                    const SizedBox(height: 24),
                    TextField(
                      controller: _username,
                      decoration: const InputDecoration(labelText: '用户名或邮箱'),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _password,
                      decoration: const InputDecoration(labelText: '密码'),
                      obscureText: true,
                    ),
                    if (widget.appState.turnstileSiteKey.isNotEmpty) ...[
                      const SizedBox(height: 12),
                      TurnstileBox(
                        key: ValueKey('login-turnstile-$_turnstileReset'),
                        siteKey: widget.appState.turnstileSiteKey,
                        onTokenChanged: (token) => _turnstileToken = token,
                      ),
                    ],
                    const SizedBox(height: 24),
                    SizedBox(
                      width: double.infinity,
                      child: ElevatedButton(
                        style: ElevatedButton.styleFrom(
                          backgroundColor: const Color(0xFFB91C1C),
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.symmetric(vertical: 14),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(16),
                          ),
                        ),
                        onPressed: _loading ? null : _handleLogin,
                        child: _loading
                            ? const SizedBox(
                                height: 20,
                                width: 20,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Text('登录'),
                      ),
                    ),
                    if (widget.appState.githubLoginEnabled) ...[
                      const SizedBox(height: 10),
                      SizedBox(
                        width: double.infinity,
                        child: OutlinedButton.icon(
                          onPressed: _githubLoading ? null : _handleGithubLogin,
                          style: OutlinedButton.styleFrom(
                            foregroundColor: const Color(0xFFB91C1C),
                            side: const BorderSide(color: Color(0xFFB91C1C)),
                            padding: const EdgeInsets.symmetric(vertical: 14),
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(16),
                            ),
                          ),
                          icon: _githubLoading
                              ? const SizedBox(
                                  height: 18,
                                  width: 18,
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                )
                              : const Icon(Icons.code),
                          label: const Text('使用 GitHub 登录'),
                        ),
                      ),
                    ],
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        TextButton(
                          style: TextButton.styleFrom(
                            foregroundColor: const Color(0xFFB91C1C),
                          ),
                          onPressed: () {
                            Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) =>
                                    RegisterScreen(appState: widget.appState),
                              ),
                            );
                          },
                          child: const Text('没有账号？立即注册'),
                        ),
                        const Spacer(),
                        TextButton(
                          style: TextButton.styleFrom(
                            foregroundColor: const Color(0xFFB91C1C),
                          ),
                          onPressed: () {
                            Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) => ForgotPasswordScreen(
                                  appState: widget.appState,
                                ),
                              ),
                            );
                          },
                          child: const Text('忘记密码？'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class ForgotPasswordScreen extends StatefulWidget {
  const ForgotPasswordScreen({super.key, required this.appState});

  final AppState appState;

  @override
  State<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
}

class _ForgotPasswordScreenState extends State<ForgotPasswordScreen> {
  final TextEditingController _email = TextEditingController();
  bool _loading = false;
  String _turnstileToken = '';
  int _turnstileReset = 0;

  @override
  void dispose() {
    _email.dispose();
    super.dispose();
  }

  Future<void> _handleSubmit() async {
    if (widget.appState.turnstileSiteKey.isNotEmpty && _turnstileToken.isEmpty) {
      _showMessage('请先完成人机验证');
      return;
    }
    setState(() => _loading = true);
    try {
      final res = await ApiClient.instance.forgotPassword(
        email: _email.text.trim(),
        turnstileToken: _turnstileToken,
      );
      if (!mounted) return;
      if (res['success'] == true) {
        await showDialog<void>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('邮件已发送'),
            content: Text(
              res['message']?.toString() ?? '请到邮箱查看重置密码链接',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('知道了'),
              ),
            ],
          ),
        );
        if (mounted) {
          Navigator.of(context).pop();
        }
      } else {
        _resetTurnstile();
        _showMessage(res['message']?.toString() ?? '提交失败');
      }
    } catch (e) {
      if (mounted) {
        _resetTurnstile();
        _showMessage('提交失败: $e');
      }
    } finally {
      if (mounted) {
        setState(() => _loading = false);
      }
    }
  }

  void _resetTurnstile() {
    setState(() {
      _turnstileToken = '';
      _turnstileReset++;
    });
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('找回密码')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _email,
              keyboardType: TextInputType.emailAddress,
              decoration: const InputDecoration(labelText: '邮箱'),
            ),
            if (widget.appState.turnstileSiteKey.isNotEmpty) ...[
              const SizedBox(height: 12),
              TurnstileBox(
                key: ValueKey('forgot-turnstile-$_turnstileReset'),
                siteKey: widget.appState.turnstileSiteKey,
                onTokenChanged: (token) => _turnstileToken = token,
              ),
            ],
            const SizedBox(height: 24),
            ElevatedButton(
              onPressed: _loading ? null : _handleSubmit,
              child: _loading
                  ? const SizedBox(
                      height: 20,
                      width: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('发送重置邮件'),
            ),
          ],
        ),
      ),
    );
  }
}

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key, required this.appState});

  final AppState appState;

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final TextEditingController _username = TextEditingController();
  final TextEditingController _email = TextEditingController();
  final TextEditingController _password = TextEditingController();
  final TextEditingController _confirmPassword = TextEditingController();
  final TextEditingController _inviteCode = TextEditingController();
  bool _loading = false;
  String _turnstileToken = '';
  int _turnstileReset = 0;

  @override
  void dispose() {
    _username.dispose();
    _email.dispose();
    _password.dispose();
    _confirmPassword.dispose();
    _inviteCode.dispose();
    super.dispose();
  }

  Future<void> _handleRegister() async {
    if (widget.appState.turnstileSiteKey.isNotEmpty && _turnstileToken.isEmpty) {
      _showMessage('请先完成人机验证');
      return;
    }
    setState(() => _loading = true);
    final error = await widget.appState.register(
      username: _username.text.trim(),
      email: _email.text.trim(),
      password: _password.text,
      confirmPassword: _confirmPassword.text,
      inviteCode: _inviteCode.text.trim(),
      turnstileToken: _turnstileToken,
    );
    setState(() => _loading = false);

    if (!mounted) return;
    if (error != null) {
      setState(() {
        _turnstileToken = '';
        _turnstileReset++;
      });
      _showMessage(error);
      return;
    }

    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('注册成功'),
        content: const Text('注册完成，请登录并激活账号。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('知道了'),
          ),
        ],
      ),
    );
    if (mounted) {
      Navigator.of(context).pop();
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  String _formatYuan(num? value) {
    if (value == null) return '-';
    return '${value.toStringAsFixed(0)}元';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('注册账号')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            TextField(
              controller: _username,
              decoration: const InputDecoration(labelText: '用户名'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _email,
              decoration: const InputDecoration(labelText: '邮箱'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _password,
              decoration: const InputDecoration(labelText: '密码'),
              obscureText: true,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _confirmPassword,
              decoration: const InputDecoration(labelText: '确认密码'),
              obscureText: true,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _inviteCode,
              decoration: const InputDecoration(
                labelText: '邀请码（可选）',
              ),
            ),
            if (widget.appState.turnstileSiteKey.isNotEmpty) ...[
              const SizedBox(height: 12),
              TurnstileBox(
                key: ValueKey('register-turnstile-$_turnstileReset'),
                siteKey: widget.appState.turnstileSiteKey,
                onTokenChanged: (token) => _turnstileToken = token,
              ),
            ],
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: _loading ? null : _handleRegister,
                child: _loading
                    ? const SizedBox(
                        height: 20,
                        width: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('注册'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.appState});

  final AppState appState;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final screens = [
      const RecordsScreen(),
      const ZodiacNumbersScreen(),
      ManualPickScreen(appState: widget.appState),
      PredictScreen(appState: widget.appState),
      ProfileScreen(appState: widget.appState),
    ];

    return Scaffold(
      body: screens[_index],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (value) => setState(() => _index = value),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.list_alt), label: '开奖记录'),
          NavigationDestination(icon: Icon(Icons.grid_view), label: '生肖号码'),
          NavigationDestination(icon: Icon(Icons.touch_app), label: '手动选号'),
          NavigationDestination(icon: Icon(Icons.auto_graph), label: '号码预测'),
          NavigationDestination(icon: Icon(Icons.person), label: '个人中心'),
        ],
      ),
    );
  }
}

Color ballColor(String number) {
  final numValue = int.tryParse(number) ?? 0;
  const red = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46];
  const blue = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48];
  if (red.contains(numValue)) return const Color(0xFFE54B4B);
  if (blue.contains(numValue)) return const Color(0xFF2D6CDF);
  return const Color(0xFF36B37E);
}

String ballColorName(String number) {
  final numValue = int.tryParse(number) ?? 0;
  const red = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46];
  const blue = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48];
  if (red.contains(numValue)) return '红';
  if (blue.contains(numValue)) return '蓝';
  return '绿';
}

class ZodiacNumbersScreen extends StatefulWidget {
  const ZodiacNumbersScreen({super.key});

  @override
  State<ZodiacNumbersScreen> createState() => _ZodiacNumbersScreenState();
}

class _ZodiacNumbersScreenState extends State<ZodiacNumbersScreen> {
  List<_ZodiacNumberItem> _items = [];
  bool _loading = false;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    setState(() {
      _loading = true;
      _errorMessage = null;
    });
    final numbers = List.generate(49, (index) => '${index + 1}');
    try {
      final res = await ApiClient.instance.getZodiacs(
        numbers: numbers,
        region: 'hk',
        year: DateTime.now().year.toString(),
      );
      final normal =
          (res['normal_zodiacs'] as List<dynamic>? ?? []).map((e) {
        return e.toString();
      }).toList();
      final special = res['special_zodiac']?.toString() ?? '';
      final items = <_ZodiacNumberItem>[];
      for (var i = 0; i < numbers.length; i++) {
        String zodiac = '';
        if (i < numbers.length - 1) {
          zodiac = i < normal.length ? normal[i] : '';
        } else {
          zodiac = special;
        }
        items.add(
          _ZodiacNumberItem(
            number: numbers[i],
            zodiac: zodiac.isEmpty ? '-' : zodiac,
          ),
        );
      }
      if (!mounted) return;
      setState(() {
        _items = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _errorMessage = '获取生肖号码失败: $e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
  appBar: AppBar(title: const Text('生肖号码表')),
      body: RefreshIndicator(
        onRefresh: _fetch,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _errorMessage != null
                ? ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    children: [
                      const SizedBox(height: 120),
                      Center(
                        child: Text(
                          _errorMessage!,
                          style: TextStyle(color: Colors.red.shade400),
                        ),
                      ),
                      const SizedBox(height: 16),
                      Center(
                        child: ElevatedButton(
                          onPressed: _fetch,
                          child: const Text('重试'),
                        ),
                      ),
                    ],
                  )
                : LayoutBuilder(
                    builder: (context, constraints) {
                      final columns = constraints.maxWidth >= 600 ? 7 : 5;
                      final compact = columns == 7;
                      return GridView.builder(
                        padding: const EdgeInsets.all(8),
                        physics: const AlwaysScrollableScrollPhysics(),
                        gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                          crossAxisCount: columns,
                          mainAxisSpacing: compact ? 6 : 8,
                          crossAxisSpacing: compact ? 6 : 8,
                          childAspectRatio: compact ? 0.98 : 1.02,
                        ),
                        itemCount: _items.length,
                        itemBuilder: (context, index) {
                          final item = _items[index];
                          final color = ballColor(item.number);
                          return Container(
                            decoration: BoxDecoration(
                              color: Colors.white,
                              borderRadius: BorderRadius.circular(12),
                              boxShadow: [
                                BoxShadow(
                                  color: Colors.black.withOpacity(0.06),
                                  blurRadius: 8,
                                  offset: const Offset(0, 3),
                                ),
                              ],
                            ),
                            child: Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                _Ball(
                                  number: item.number,
                                  color: color,
                                  size: compact ? 32 : 34,
                                  fontSize: 12,
                                ),
                                const SizedBox(height: 4),
                                Text(
                                  item.zodiac,
                                  style: TextStyle(
                                    fontSize: compact ? 11 : 12,
                                    fontWeight: FontWeight.w600,
                                    color: Colors.grey.shade700,
                                  ),
                                ),
                              ],
                            ),
                          );
                        },
                      );
                    },
                  ),
      ),
    );
  }
}

class _ZodiacNumberItem {
  const _ZodiacNumberItem({
    required this.number,
    required this.zodiac,
  });

  final String number;
  final String zodiac;
}

class ManualPickScreen extends StatefulWidget {
  const ManualPickScreen({super.key, required this.appState});

  final AppState appState;

  @override
  State<ManualPickScreen> createState() => _ManualPickScreenState();
}

class _ManualBetOutcome {
  const _ManualBetOutcome({
    required this.label,
    required this.win,
    required this.profit,
    required this.stake,
    required this.odds,
  });

  final String label;
  final bool win;
  final double profit;
  final double stake;
  final double odds;
}

class _ManualPickScreenState extends State<ManualPickScreen> {
  static const _zodiacOptions = [
    '鼠',
    '牛',
    '虎',
    '兔',
    '龙',
    '蛇',
    '马',
    '羊',
    '猴',
    '鸡',
    '狗',
    '猪',
  ];
  static const _colorOptions = ['红', '蓝', '绿'];
  static const _parityOptions = ['单', '双'];

  final Set<int> _selectedNumbers = {};
  final Set<String> _selectedZodiacs = {};
  final Set<String> _selectedColors = {};
  final Set<String> _selectedParity = {};

  final TextEditingController _periodController = TextEditingController();
  final TextEditingController _bettorController = TextEditingController();
  final TextEditingController _stakeSpecialController =
      TextEditingController(text: '10');
  final TextEditingController _stakeCommonController =
      TextEditingController(text: '10');
  final TextEditingController _numberOddsController =
      TextEditingController(text: '48');
  final TextEditingController _zodiacOddsController =
      TextEditingController(text: '12');
  final TextEditingController _colorOddsController =
      TextEditingController(text: '2');
  final TextEditingController _parityOddsController =
      TextEditingController(text: '2');
  final Map<int, TextEditingController> _numberStakeControllers = {};

  String _region = 'macau';
  DrawRecord? _latestDraw;
  String _nextPeriod = '';
  bool _loading = false;
  bool _settling = false;
  String? _statusMessage;
  int? _pendingRecordId;
  List<_ManualBetOutcome> _outcomes = [];
  double _totalStake = 0;
  double _totalProfit = 0;
  bool _loadingBets = false;
  List<Map<String, dynamic>> _manualBets = [];
  bool _showAllManualBetPeriods = false;

  bool get _activationValid => widget.appState.activationValid;

  String _oddsPrefKey(String name) {
    final user = widget.appState.user;
    final suffix = user == null ? 'guest' : 'user_${user.id}';
    return 'manual_odds_${name}_$suffix';
  }

  Future<void> _loadOddsPrefs() async {
    final prefs = await SharedPreferences.getInstance();
    final number = prefs.getString(_oddsPrefKey('number'));
    final zodiac = prefs.getString(_oddsPrefKey('zodiac'));
    final color = prefs.getString(_oddsPrefKey('color'));
    final parity = prefs.getString(_oddsPrefKey('parity'));
    if (number != null && number.isNotEmpty) {
      _numberOddsController.text = number;
    }
    if (zodiac != null && zodiac.isNotEmpty) {
      _zodiacOddsController.text = zodiac;
    }
    if (color != null && color.isNotEmpty) {
      _colorOddsController.text = color;
    }
    if (parity != null && parity.isNotEmpty) {
      _parityOddsController.text = parity;
    }
  }

  Future<void> _saveOddsPrefs() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _oddsPrefKey('number'),
      _numberOddsController.text.trim(),
    );
    await prefs.setString(
      _oddsPrefKey('zodiac'),
      _zodiacOddsController.text.trim(),
    );
    await prefs.setString(
      _oddsPrefKey('color'),
      _colorOddsController.text.trim(),
    );
    await prefs.setString(
      _oddsPrefKey('parity'),
      _parityOddsController.text.trim(),
    );
  }

  Future<bool> _requireActivation() async {
    if (_activationValid) return true;
    await showActivationDialog(context, widget.appState);
    if (mounted) {
      setState(() {});
    }
    return _activationValid;
  }

  Future<void> _promptActivation() async {
    await showActivationDialog(context, widget.appState);
    if (mounted) {
      setState(() {});
    }
  }

  String _betType = 'number';

  void _clearPending() {
    if (_pendingRecordId != null) {
      _pendingRecordId = null;
    }
  }

  void _resetBetSelection() {
    _betType = 'number';
    _selectedNumbers.clear();
    _selectedZodiacs.clear();
    _selectedColors.clear();
    _selectedParity.clear();
    for (final controller in _numberStakeControllers.values) {
      controller.dispose();
    }
    _numberStakeControllers.clear();
    _bettorController.clear();
  }

  void _syncNumberStakeControllers() {
    final removed = _numberStakeControllers.keys
        .where((number) => !_selectedNumbers.contains(number))
        .toList();
    for (final number in removed) {
      _numberStakeControllers.remove(number)?.dispose();
    }
    for (final number in _selectedNumbers) {
      _numberStakeControllers.putIfAbsent(
        number,
        () => TextEditingController(text: ''),
      );
    }
  }

  Map<int, double> _collectNumberStakes() {
    final result = <int, double>{};
    for (final number in _selectedNumbers) {
      final controller = _numberStakeControllers[number];
      final amount = double.tryParse(controller?.text.trim() ?? '') ?? 0;
      if (amount > 0) {
        result[number] = amount;
      }
    }
    return result;
  }

  List<Map<String, dynamic>> _buildNumberStakePayload() {
    final payload = <Map<String, dynamic>>[];
    for (final entry in _collectNumberStakes().entries) {
      payload.add({'number': entry.key, 'stake': entry.value});
    }
    return payload;
  }

  String _formatNumberStakesText(String raw) {
    if (!raw.contains(':')) return raw;
    final parts = raw.split(',');
    final formatted = <String>[];
    for (final part in parts) {
      final trimmed = part.trim();
      if (trimmed.isEmpty || !trimmed.contains(':')) continue;
      final items = trimmed.split(':');
      if (items.length < 2) continue;
      final amount = double.tryParse(items[1]) ?? 0;
      formatted.add('${items[0]}(${amount.toStringAsFixed(0)}元)');
    }
    return formatted.join(', ');
  }

  List<Map<String, dynamic>> _parseNumberStakeEntries(String raw) {
    if (!raw.contains(':')) return [];
    final parts = raw.split(',');
    final entries = <Map<String, dynamic>>[];
    for (final part in parts) {
      final trimmed = part.trim();
      if (trimmed.isEmpty || !trimmed.contains(':')) continue;
      final items = trimmed.split(':');
      if (items.length < 2) continue;
      final number = items[0].trim();
      final amount = double.tryParse(items[1].trim()) ?? 0;
      if (number.isEmpty) continue;
      entries.add({'number': number, 'amount': amount});
    }
    return entries;
  }

  List<Map<String, dynamic>> _parseCommonStakeEntries(String raw) {
    if (!raw.contains(':')) return [];
    final parts = raw.split(',');
    final entries = <Map<String, dynamic>>[];
    for (final part in parts) {
      final trimmed = part.trim();
      if (trimmed.isEmpty || !trimmed.contains(':')) continue;
      final items = trimmed.split(':');
      if (items.length < 2) continue;
      final label = items[0].trim();
      final amount = double.tryParse(items[1].trim()) ?? 0;
      if (label.isEmpty) continue;
      entries.add({'label': label, 'amount': amount});
    }
    return entries;
  }

  String _formatCommonStakesText(String raw) {
    if (!raw.contains(':')) return raw;
    final parts = raw.split(',');
    final formatted = <String>[];
    for (final part in parts) {
      final trimmed = part.trim();
      if (trimmed.isEmpty || !trimmed.contains(':')) continue;
      final items = trimmed.split(':');
      if (items.length < 2) continue;
      final amount = double.tryParse(items[1]) ?? 0;
      formatted.add('${items[0]}(${amount.toStringAsFixed(0)}元)');
    }
    return formatted.join(', ');
  }

  Widget _buildStakeBadge(double amount) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: const Color(0x1AF4B547),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: const Color(0x55F4B547)),
      ),
      child: Text(
        _formatYuan(amount),
        style: const TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w700,
          color: Color(0xFF92400E),
        ),
      ),
    );
  }

  Widget _buildBetLabelChip(String text, Color background, Color foreground) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: foreground.withOpacity(0.18)),
      ),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w700,
          color: foreground,
        ),
      ),
    );
  }

  Widget _buildProfitBadge(double? amount) {
    final value = amount ?? 0;
    final isPending = amount == null;
    final isWin = value > 0;
    final isLose = value < 0;
    final background = isPending
        ? const Color(0x1494A3B8)
        : isWin
            ? const Color(0x182563EB)
            : isLose
                ? const Color(0x16EF4444)
                : const Color(0x1494A3B8);
    final foreground = isPending
        ? const Color(0xFF64748B)
        : isWin
            ? const Color(0xFF2563EB)
            : isLose
                ? const Color(0xFFDC2626)
                : const Color(0xFF64748B);
    final text = isPending
        ? '-'
        : isWin
            ? '+${_formatYuan(value)}'
            : _formatYuan(value);

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: foreground.withOpacity(0.18)),
      ),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w800,
          color: foreground,
        ),
      ),
    );
  }

  Widget _buildCommonStakeWrap(
    String title,
    List<Map<String, dynamic>> entries, {
    required Color chipBackground,
    required Color chipForeground,
  }) {
    if (entries.isEmpty) {
      return const SizedBox.shrink();
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('$title：'),
        const SizedBox(height: 6),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: entries.map((entry) {
            final label = entry['label']?.toString() ?? '';
            final amount = (entry['amount'] as num?)?.toDouble() ?? 0;
            return Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                _buildBetLabelChip(label, chipBackground, chipForeground),
                const SizedBox(width: 6),
                _buildStakeBadge(amount),
              ],
            );
          }).toList(),
        ),
      ],
    );
  }

  Widget _buildBetSummaryWrap({
    required double stake,
    required double? profit,
  }) {
    return Wrap(
      spacing: 10,
      runSpacing: 8,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              '总下注：',
              style: TextStyle(fontWeight: FontWeight.w700),
            ),
            _buildStakeBadge(stake),
          ],
        ),
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              '总盈亏：',
              style: TextStyle(fontWeight: FontWeight.w700),
            ),
            const SizedBox(width: 6),
            _buildProfitBadge(profit),
          ],
        ),
      ],
    );
  }

  String _formatYuan(num? value) {
    if (value == null) return '-';
    return '${value.toStringAsFixed(0)}元';
  }

  Widget _buildManualBetItem(Map<String, dynamic> item) {
    final status = item['status']?.toString() ?? 'pending';
    final bettor = item['bettor_name']?.toString() ?? '';
    final createdAt = item['created_at']?.toString() ?? '';
    final recordId = item['id'];
    final numbersRaw = item['selected_numbers']?.toString() ?? '';
    final numbers = _formatNumberStakesText(numbersRaw);
    final numberEntries = _parseNumberStakeEntries(numbersRaw);
    final zodiacEntries =
        _parseCommonStakeEntries(item['selected_zodiacs']?.toString() ?? '');
    final colorEntries =
        _parseCommonStakeEntries(item['selected_colors']?.toString() ?? '');
    final parityEntries =
        _parseCommonStakeEntries(item['selected_parity']?.toString() ?? '');
    final zodiacs =
        _formatCommonStakesText(item['selected_zodiacs']?.toString() ?? '');
    final colors =
        _formatCommonStakesText(item['selected_colors']?.toString() ?? '');
    final parity =
        _formatCommonStakesText(item['selected_parity']?.toString() ?? '');
    final profit = (item['total_profit'] as num?)?.toDouble();
    final stake = (item['total_stake'] as num?)?.toDouble();
    final special = item['special_number']?.toString() ?? '';
    final specialZodiac = item['special_zodiac']?.toString() ?? '';

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: const [
          BoxShadow(
            color: Color(0x12000000),
            blurRadius: 6,
            offset: Offset(0, 2),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  '时间：$createdAt',
                  style: const TextStyle(
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: status == 'settled'
                      ? const Color(0xFFEFF6FF)
                      : const Color(0xFFFFF3E0),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  status == 'settled' ? '已结算' : '待结算',
                  style: TextStyle(
                    fontSize: 12,
                    color: status == 'settled'
                        ? const Color(0xFF2563EB)
                        : Colors.orange,
                  ),
                ),
              ),
              IconButton(
                tooltip: '删除',
                icon: const Icon(Icons.delete_outline, color: Colors.redAccent),
                onPressed: recordId == null
                    ? null
                    : () => _confirmDeleteManualBet(recordId),
              ),
            ],
          ),
          if (numberEntries.isNotEmpty)
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('号码：'),
                const SizedBox(height: 6),
                Wrap(
                  spacing: 10,
                  runSpacing: 8,
                  children: numberEntries.map((entry) {
                    final number = entry['number']?.toString() ?? '';
                    final amount = (entry['amount'] as num?) ?? 0;
                    return Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        _Ball(
                          number: number,
                          color: ballColor(number),
                          size: 26,
                          fontSize: 11,
                        ),
                        const SizedBox(height: 4),
                        _buildStakeBadge((amount as num).toDouble()),
                      ],
                    );
                  }).toList(),
                ),
              ],
            )
          else if (numbers.isNotEmpty)
            Text('号码：$numbers'),
          if (bettor.isNotEmpty) Text('下注人：$bettor'),
          if (zodiacEntries.isNotEmpty)
            _buildCommonStakeWrap(
              '生肖',
              zodiacEntries,
              chipBackground: const Color(0x14A855F7),
              chipForeground: const Color(0xFF7E22CE),
            )
          else if (zodiacs.isNotEmpty)
            Text('生肖：$zodiacs'),
          if (colorEntries.isNotEmpty)
            _buildCommonStakeWrap(
              '波色',
              colorEntries,
              chipBackground: const Color(0x140EA5E9),
              chipForeground: const Color(0xFF0369A1),
            )
          else if (colors.isNotEmpty)
            Text('波色：$colors'),
          if (parityEntries.isNotEmpty)
            _buildCommonStakeWrap(
              '单双',
              parityEntries,
              chipBackground: const Color(0x14F59E0B),
              chipForeground: const Color(0xFFB45309),
            )
          else if (parity.isNotEmpty)
            Text('单双：$parity'),
          if (status == 'settled')
            Text('开奖结果：$special  生肖：$specialZodiac'),
          Row(
            children: [
              Expanded(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    const SizedBox(
                      width: 56,
                      child: Text(
                        '下注：',
                        style: TextStyle(fontWeight: FontWeight.w600),
                      ),
                    ),
                    _buildStakeBadge((stake ?? 0).toDouble()),
                  ],
                ),
              ),
              Expanded(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    const SizedBox(
                      width: 56,
                      child: Text(
                        '盈亏：',
                        style: TextStyle(fontWeight: FontWeight.w600),
                      ),
                    ),
                    _buildProfitBadge(profit),
                  ],
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _confirmDeleteManualBet(int recordId) async {
    final shouldDelete = await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: const Text('删除下注记录'),
            content: const Text('确定要删除这条下注记录吗？'),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(false),
                child: const Text('取消'),
              ),
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(true),
                child: const Text('删除'),
              ),
            ],
          ),
        ) ??
        false;
    if (!shouldDelete) return;
    await _deleteManualBet(recordId);
  }

  Future<void> _deleteManualBet(int recordId) async {
    try {
      final res = await ApiClient.instance.deleteManualBet(id: recordId);
      if (!mounted) return;
      if (res['success'] == true) {
        setState(() {
          _manualBets.removeWhere(
              (item) => item['id']?.toString() == recordId.toString());
        });
      } else {
        _showMessage(res['message']?.toString() ?? '删除失败');
      }
    } catch (_) {
      if (!mounted) return;
      _showMessage('删除失败');
    }
  }

  @override
  void initState() {
    super.initState();
    _loadOddsPrefs();
    _restoreRegionAndLoad();
  }

  Future<void> _restoreRegionAndLoad() async {
    final savedRegion = await _loadLastRegion();
    if (!mounted) return;
    setState(() => _region = savedRegion);
    _loadLatestDraw();
    _loadManualBets();
  }

  @override
  void dispose() {
    _periodController.dispose();
    _bettorController.dispose();
    _stakeSpecialController.dispose();
    _stakeCommonController.dispose();
    _numberOddsController.dispose();
    _zodiacOddsController.dispose();
    _colorOddsController.dispose();
    _parityOddsController.dispose();
    for (final controller in _numberStakeControllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _loadLatestDraw({
    bool showLoading = true,
    bool replacePeriod = false,
  }) async {
    setState(() {
      if (showLoading) {
        _loading = true;
      }
      _statusMessage = null;
      _pendingRecordId = null;
    });
    final draw = await _fetchLatestDraw();
    if (!mounted) return;
    if (draw == null) {
      setState(() {
        if (showLoading) {
          _loading = false;
        }
        _statusMessage = '获取最新开奖失败';
      });
      return;
    }
    final nextPeriod = _computeNextPeriod(draw.id);
    setState(() {
      if (showLoading) {
        _loading = false;
      }
      _latestDraw = draw;
      _nextPeriod = nextPeriod;
      if (replacePeriod || _periodController.text.trim().isEmpty) {
        _periodController.text = nextPeriod;
      }
    });
  }

  Future<void> _loadManualBets() async {
    setState(() => _loadingBets = true);
    try {
      final res = await ApiClient.instance.manualBets(
        region: _region,
        limit: 20,
      );
      final items = (res['items'] as List<dynamic>? ?? [])
          .whereType<Map<String, dynamic>>()
          .toList();
      if (!mounted) return;
      setState(() {
        _manualBets = items;
        _showAllManualBetPeriods = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _manualBets = [];
        _showAllManualBetPeriods = false;
      });
    } finally {
      if (mounted) {
        setState(() => _loadingBets = false);
      }
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  Future<DrawRecord?> _fetchLatestDraw() async {
    try {
      final year = DateTime.now().year.toString();
      final data =
          await ApiClient.instance.draws(region: _region, year: year);
      if (data.isEmpty) {
        return null;
      }
      return DrawRecord.fromJson(data.first as Map<String, dynamic>);
    } catch (_) {
      return null;
    }
  }

  String _computeNextPeriod(String latestId) {
    final year = DateTime.now().year;
    if (latestId.contains('/')) {
      final parts = latestId.split('/');
      if (parts.length == 2) {
        final yearPart = parts[0];
        final numPart = parts[1];
        final num = int.tryParse(numPart) ?? 0;
        if (num >= 120) {
          final yearLen = yearPart.length;
          final nextYear = (int.tryParse(yearPart) ?? (year % 100)) + 1;
          final nextYearStr = nextYear.toString().padLeft(yearLen, '0');
          return '$nextYearStr/001';
        }
        final nextNum = (num + 1).toString().padLeft(numPart.length, '0');
        return '$yearPart/$nextNum';
      }
    } else if (RegExp(r'^\d+$').hasMatch(latestId)) {
      if (latestId.length >= 7 && latestId.startsWith(RegExp(r'^\d{4}'))) {
        final yearPart = latestId.substring(0, 4);
        final seqPart = latestId.substring(4);
        final seq = int.tryParse(seqPart) ?? 0;
        final nextSeq = seq + 1;
        if (seqPart.length == 3 && nextSeq > 999) {
          final nextYear = (int.tryParse(yearPart) ?? year) + 1;
          return '$nextYear${'001'}';
        }
        return '$yearPart${nextSeq.toString().padLeft(seqPart.length, '0')}';
      }
      final num = int.tryParse(latestId) ?? 0;
      return (num + 1).toString();
    }
    return '$year/001';
  }

  Future<String> _resolveSpecialZodiac(DrawRecord draw) async {
    if (draw.rawZodiacs.length > draw.normalNumbers.length) {
      final index = draw.normalNumbers.length;
      if (index >= 0 && index < draw.rawZodiacs.length) {
        return draw.rawZodiacs[index];
      }
    }
    if (draw.specialZodiac.isNotEmpty) {
      return draw.specialZodiac;
    }
    if (draw.specialNumber.isEmpty) {
      return '';
    }
    try {
      final res = await ApiClient.instance.getZodiacs(
        numbers: [draw.specialNumber],
        region: _region,
        year: DateTime.now().year.toString(),
      );
      return res['special_zodiac']?.toString() ?? '';
    } catch (_) {
      return '';
    }
  }

  Future<void> _settleBet() async {
    if (!await _requireActivation()) return;
    setState(() {
      _settling = true;
      _statusMessage = null;
      _outcomes = [];
      _totalStake = 0;
      _totalProfit = 0;
    });
    final draw = await _fetchLatestDraw();
    if (!mounted) return;
    if (draw == null) {
      setState(() {
        _settling = false;
        _statusMessage = '获取最新开奖失败';
      });
      return;
    }

    final period = _periodController.text.trim();
    if (period.isEmpty) {
      setState(() {
        _settling = false;
        _statusMessage = '请输入期号';
      });
      return;
    }
    if (period != draw.id) {
      setState(() {
        _settling = false;
        _statusMessage = '当前期号未开奖，最新期号：${draw.id}';
      });
      return;
    }

    await _saveOddsPrefs();

    final stakeSpecial =
        double.tryParse(_stakeSpecialController.text.trim()) ?? 0;
    final stakeCommon =
        double.tryParse(_stakeCommonController.text.trim()) ?? 0;
    final betNumber = _betType == 'number';
    final betZodiac = _betType == 'zodiac';
    final betColor = _betType == 'color';
    final betParity = _betType == 'parity';

    if (betNumber && _selectedNumbers.isEmpty) {
      setState(() {
        _settling = false;
        _statusMessage = '请选择号码';
      });
      return;
    }
    if ((betZodiac || betColor || betParity) && stakeCommon <= 0) {
      setState(() {
        _settling = false;
        _statusMessage = '请输入有效的共用下注金额';
      });
      return;
    }

    if (betNumber) {
      final numberStakes = _collectNumberStakes();
      if (numberStakes.length != _selectedNumbers.length) {
        setState(() {
          _settling = false;
          _statusMessage = '请为每个号码填写下注金额';
        });
        return;
      }
    }
    if (betZodiac && _selectedZodiacs.isEmpty) {
      setState(() {
        _settling = false;
        _statusMessage = '请选择生肖';
      });
      return;
    }
    if (betColor && _selectedColors.isEmpty) {
      setState(() {
        _settling = false;
        _statusMessage = '请选择波色';
      });
      return;
    }
    if (betParity && _selectedParity.isEmpty) {
      setState(() {
        _settling = false;
        _statusMessage = '请选择单双';
      });
      return;
    }

    final specialNumber = draw.specialNumber;
    final specialZodiac = await _resolveSpecialZodiac(draw);
    final specialColor = ballColorName(specialNumber);
    final specialParity =
        (int.tryParse(specialNumber) ?? 0) % 2 == 0 ? '双' : '单';

    final outcomes = <_ManualBetOutcome>[];
    double totalStake = 0;
    double totalProfit = 0;

    if (betNumber) {
      final odds = double.tryParse(_numberOddsController.text.trim()) ?? 0;
      final numberStakes = _collectNumberStakes();
      final totalNumberStake =
          numberStakes.values.fold(0.0, (sum, value) => sum + value);
      final hitStake = numberStakes[int.tryParse(specialNumber)] ?? 0;
      final win = hitStake > 0;
      final profit = hitStake * odds - totalNumberStake;
      outcomes.add(_ManualBetOutcome(
        label: '号码',
        win: win,
        profit: profit,
        stake: totalNumberStake,
        odds: odds,
      ));
      totalStake += totalNumberStake;
      totalProfit += profit;
    }
    if (betZodiac) {
      final odds = double.tryParse(_zodiacOddsController.text.trim()) ?? 0;
      final win = specialZodiac.isNotEmpty && _selectedZodiacs.contains(specialZodiac);
      final profit =
          win ? stakeCommon * odds - stakeCommon : -stakeCommon;
      outcomes.add(_ManualBetOutcome(
        label: '生肖',
        win: win,
        profit: profit,
        stake: stakeCommon,
        odds: odds,
      ));
      totalStake += stakeCommon;
      totalProfit += profit;
    }
    if (betColor) {
      final odds = double.tryParse(_colorOddsController.text.trim()) ?? 0;
      final win = specialColor.isNotEmpty && _selectedColors.contains(specialColor);
      final profit =
          win ? stakeCommon * odds - stakeCommon : -stakeCommon;
      outcomes.add(_ManualBetOutcome(
        label: '波色',
        win: win,
        profit: profit,
        stake: stakeCommon,
        odds: odds,
      ));
      totalStake += stakeCommon;
      totalProfit += profit;
    }
    if (betParity) {
      final odds = double.tryParse(_parityOddsController.text.trim()) ?? 0;
      final win = _selectedParity.contains(specialParity);
      final profit =
          win ? stakeCommon * odds - stakeCommon : -stakeCommon;
      outcomes.add(_ManualBetOutcome(
        label: '单双',
        win: win,
        profit: profit,
        stake: stakeCommon,
        odds: odds,
      ));
      totalStake += stakeCommon;
      totalProfit += profit;
    }

    String? saveError;
    try {
      final response = await ApiClient.instance.createManualBet(
        region: _region,
        period: period,
        settle: true,
        recordId: _pendingRecordId,
        bettorName: _bettorController.text,
        betNumber: betNumber,
        betZodiac: betZodiac,
        betColor: betColor,
        betParity: betParity,
        numbers: betNumber ? _selectedNumbers.toList() : <int>[],
        numberStakes: betNumber ? _buildNumberStakePayload() : null,
        zodiacs: betZodiac ? _selectedZodiacs.toList() : <String>[],
        colors: betColor ? _selectedColors.toList() : <String>[],
        parity: betParity ? _selectedParity.toList() : <String>[],
        stakeSpecial: _stakeSpecialController.text.trim(),
        stakeCommon: _stakeCommonController.text.trim(),
        oddsNumber: _numberOddsController.text.trim(),
        oddsZodiac: _zodiacOddsController.text.trim(),
        oddsColor: _colorOddsController.text.trim(),
        oddsParity: _parityOddsController.text.trim(),
      );
      if (response['success'] != true) {
        saveError = response['message']?.toString() ?? '记录保存失败';
      }
    } catch (_) {
      saveError = '记录保存失败';
    }

    if (!mounted) return;
    setState(() {
      _settling = false;
      _latestDraw = draw;
      _outcomes = outcomes;
      _totalStake = totalStake;
      _totalProfit = totalProfit;
      if (saveError != null) {
        _statusMessage = saveError;
      } else {
        _pendingRecordId = null;
        _loadManualBets();
      }
    });
  }

  Future<void> _submitBet() async {
    if (!await _requireActivation()) return;
    setState(() {
      _settling = true;
      _statusMessage = null;
    });
    final period = _periodController.text.trim();
    if (period.isEmpty) {
      setState(() {
        _settling = false;
        _statusMessage = '请输入期号';
      });
      return;
    }

    await _saveOddsPrefs();

    final stakeSpecial =
        double.tryParse(_stakeSpecialController.text.trim()) ?? 0;
    final stakeCommon =
        double.tryParse(_stakeCommonController.text.trim()) ?? 0;
    final betNumber = _betType == 'number';
    final betZodiac = _betType == 'zodiac';
    final betColor = _betType == 'color';
    final betParity = _betType == 'parity';

    if (betNumber && _selectedNumbers.isEmpty) {
      setState(() {
        _settling = false;
        _statusMessage = '请选择号码';
      });
      return;
    }
    if ((betZodiac || betColor || betParity) && stakeCommon <= 0) {
      setState(() {
        _settling = false;
        _statusMessage = '请输入有效的共用下注金额';
      });
      return;
    }
    if (betNumber) {
      final numberStakes = _collectNumberStakes();
      if (numberStakes.length != _selectedNumbers.length) {
        setState(() {
          _settling = false;
          _statusMessage = '请为每个号码填写下注金额';
        });
        return;
      }
    }
    if (betZodiac && _selectedZodiacs.isEmpty) {
      setState(() {
        _settling = false;
        _statusMessage = '请选择生肖';
      });
      return;
    }
    if (betColor && _selectedColors.isEmpty) {
      setState(() {
        _settling = false;
        _statusMessage = '请选择波色';
      });
      return;
    }
    if (betParity && _selectedParity.isEmpty) {
      setState(() {
        _settling = false;
        _statusMessage = '请选择单双';
      });
      return;
    }

    try {
      final response = await ApiClient.instance.createManualBet(
        region: _region,
        period: period,
        settle: false,
        bettorName: _bettorController.text,
        betNumber: betNumber,
        betZodiac: betZodiac,
        betColor: betColor,
        betParity: betParity,
        numbers: betNumber ? _selectedNumbers.toList() : <int>[],
        numberStakes: betNumber ? _buildNumberStakePayload() : null,
        zodiacs: betZodiac ? _selectedZodiacs.toList() : <String>[],
        colors: betColor ? _selectedColors.toList() : <String>[],
        parity: betParity ? _selectedParity.toList() : <String>[],
        stakeSpecial: _stakeSpecialController.text.trim(),
        stakeCommon: _stakeCommonController.text.trim(),
        oddsNumber: _numberOddsController.text.trim(),
        oddsZodiac: _zodiacOddsController.text.trim(),
        oddsColor: _colorOddsController.text.trim(),
        oddsParity: _parityOddsController.text.trim(),
      );
      if (response['success'] == true) {
        setState(() {
          _pendingRecordId = response['record_id'] as int?;
          _statusMessage = '已保存下注记录';
          _resetBetSelection();
        });
        _loadManualBets();
      } else {
        setState(() {
          _statusMessage = response['message']?.toString() ?? '下注保存失败';
        });
      }
    } catch (_) {
      setState(() => _statusMessage = '下注保存失败');
    } finally {
      if (mounted) {
        setState(() => _settling = false);
      }
    }
  }

  Widget _buildNumberGrid() {
    final numbers = List.generate(49, (index) => index + 1);
    return GridView.builder(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      itemCount: numbers.length,
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 7,
        mainAxisSpacing: 6,
        crossAxisSpacing: 6,
        childAspectRatio: 1,
      ),
      itemBuilder: (context, index) {
        final number = numbers[index];
        final selected = _selectedNumbers.contains(number);
        final color = ballColor(number.toString());
        return GestureDetector(
          onTap: () {
            setState(() {
              if (selected) {
                _selectedNumbers.remove(number);
                _numberStakeControllers.remove(number)?.dispose();
              } else {
                _selectedNumbers.add(number);
              }
              _syncNumberStakeControllers();
              _clearPending();
            });
          },
          child: Container(
            decoration: BoxDecoration(
              color: selected ? color.withOpacity(0.12) : Colors.white,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(
                color: selected ? color : Colors.grey.shade300,
                width: selected ? 1.5 : 1,
              ),
            ),
            alignment: Alignment.center,
            child: _Ball(
              number: number.toString(),
              color: color,
              size: 30,
              fontSize: 12,
            ),
          ),
        );
      },
    );
  }

  Color _regionColor(String value) {
    return value == 'macau' ? const Color(0xFF2563EB) : const Color(0xFFB91C1C);
  }

  Widget _buildRegionButton(String value, String label) {
    final selected = _region == value;
    final color = _regionColor(value);
    return Expanded(
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () async {
          if (_region == value) return;
          await _saveLastRegion(value);
          if (!mounted) return;
          setState(() {
            _region = value;
            _latestDraw = null;
            _nextPeriod = '';
            _periodController.clear();
            _clearPending();
          });
          _loadLatestDraw(showLoading: false, replacePeriod: true);
          _loadManualBets();
        },
        child: Container(
          height: 48,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected ? color : color.withOpacity(0.10),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: selected ? color : color.withOpacity(0.35),
              width: selected ? 1.5 : 1,
            ),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                label,
                style: TextStyle(
                  color: selected ? Colors.white : color,
                  fontWeight: FontWeight.w800,
                  fontSize: 16,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Color _betTypeColor(String value) {
    switch (value) {
      case 'zodiac':
        return const Color(0xFF7C3AED);
      case 'color':
        return const Color(0xFF0EA5E9);
      case 'parity':
        return const Color(0xFF16A34A);
      case 'number':
      default:
        return const Color(0xFFB91C1C);
    }
  }

  Widget _buildBetTypeButton(String value, String label) {
    final selected = _betType == value;
    final color = _betTypeColor(value);
    return Expanded(
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () {
          if (_betType == value) return;
          setState(() {
            _betType = value;
            _clearPending();
          });
        },
        child: Container(
          height: 46,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected ? color : color.withOpacity(0.10),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: selected ? color : color.withOpacity(0.35),
              width: selected ? 1.5 : 1,
            ),
          ),
          child: Text(
            label,
            style: TextStyle(
              color: selected ? Colors.white : color,
              fontWeight: FontWeight.w900,
              fontSize: 14,
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildOddsAndBettorRow(
    TextEditingController oddsController,
    String oddsLabel,
  ) {
    return Row(
      children: [
        Expanded(
          child: TextField(
            controller: oddsController,
            onChanged: (_) => setState(_clearPending),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
              labelText: oddsLabel,
              border: const OutlineInputBorder(),
            ),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: TextField(
            controller: _bettorController,
            onChanged: (_) => setState(_clearPending),
            decoration: const InputDecoration(
              labelText: '下注人（选填）',
              border: OutlineInputBorder(),
            ),
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final activationValid = _activationValid;
    return Scaffold(
      appBar: AppBar(title: const Text('手动选号')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _loadLatestDraw,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  if (!activationValid) ...[
                    Card(
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(16),
                      ),
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Row(
                          children: [
                            const Icon(Icons.lock, color: Colors.orange),
                            const SizedBox(width: 8),
                            const Expanded(
                              child: Text(
                                '账号未激活或已过期，请先激活后使用手动选号功能。',
                                style: TextStyle(color: Colors.orange),
                              ),
                            ),
                            TextButton(
                              onPressed: _promptActivation,
                              child: const Text('去激活'),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 12),
                  ],
                  Row(
                    children: [
                      _buildRegionButton('macau', '澳门'),
                      const SizedBox(width: 10),
                      _buildRegionButton('hk', '香港'),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: _periodController,
                          onChanged: (_) => setState(_clearPending),
                          decoration: InputDecoration(
                            labelText: '期号（默认下一期）',
                            hintText: _nextPeriod,
                            border: const OutlineInputBorder(),
                          ),
                        ),
                      ),
                      const SizedBox(width: 12),
                      IconButton(
                        onPressed: () => _loadLatestDraw(),
                        icon: const Icon(Icons.refresh),
                      ),
                    ],
                  ),
                  if (_latestDraw != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(
                        '最新期号：${_latestDraw!.id}  开奖：${_latestDraw!.specialNumber}'
                        '  生肖：${_latestDraw!.specialZodiac.isNotEmpty ? _latestDraw!.specialZodiac : '-'}'
                        '  波色：${ballColorName(_latestDraw!.specialNumber)}'
                        '  单双：${(int.tryParse(_latestDraw!.specialNumber) ?? 0) % 2 == 0 ? '双' : '单'}',
                        style: TextStyle(color: Colors.grey.shade600),
                      ),
                    ),
                  const SizedBox(height: 16),
                  const Text(
                    '下注类型',
                    style: TextStyle(fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      _buildBetTypeButton('number', '号码'),
                      const SizedBox(width: 8),
                      _buildBetTypeButton('zodiac', '生肖'),
                      const SizedBox(width: 8),
                      _buildBetTypeButton('color', '波色'),
                      const SizedBox(width: 8),
                      _buildBetTypeButton('parity', '单双'),
                    ],
                  ),
                  if (_betType == 'number') ...[
                    const SizedBox(height: 16),
                    const Text(
                      '选择号码（视为特码）',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    _buildNumberGrid(),
                  ],
                  if (_betType == 'zodiac') ...[
                    const SizedBox(height: 16),
                    const Text(
                      '生肖下注',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    LayoutBuilder(
                      builder: (context, constraints) {
                        const columns = 6;
                        const spacing = 8.0;
                        final itemWidth =
                            (constraints.maxWidth - spacing * (columns - 1)) /
                                columns;
                        return Wrap(
                          spacing: spacing,
                          runSpacing: spacing,
                          children: _zodiacOptions.map((zodiac) {
                            final selected = _selectedZodiacs.contains(zodiac);
                            return SizedBox(
                              width: itemWidth,
                              child: FilterChip(
                                label: Center(child: Text(zodiac)),
                                selected: selected,
                                showCheckmark: false,
                                padding: EdgeInsets.zero,
                                onSelected: (_) {
                                  setState(() {
                                    if (selected) {
                                      _selectedZodiacs.remove(zodiac);
                                    } else {
                                      _selectedZodiacs.add(zodiac);
                                    }
                                    _clearPending();
                                  });
                                },
                              ),
                            );
                          }).toList(),
                        );
                      },
                    ),
                  ],
                  if (_betType == 'color') ...[
                    const SizedBox(height: 16),
                    const Text(
                      '波色下注',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    LayoutBuilder(
                      builder: (context, constraints) {
                        const columns = 3;
                        const spacing = 8.0;
                        final itemWidth =
                            (constraints.maxWidth - spacing * (columns - 1)) /
                                columns;
                        return Wrap(
                          spacing: spacing,
                          runSpacing: spacing,
                          children: _colorOptions.map((color) {
                            final selected = _selectedColors.contains(color);
                            final chipColor = color == '红'
                                ? const Color(0xFFE54B4B)
                                : color == '蓝'
                                    ? const Color(0xFF2D6CDF)
                                    : const Color(0xFF36B37E);
                            return SizedBox(
                              width: itemWidth,
                              child: FilterChip(
                                label: Center(child: Text(color)),
                                selected: selected,
                                showCheckmark: false,
                                selectedColor: chipColor.withOpacity(0.15),
                                padding: EdgeInsets.zero,
                                onSelected: (_) {
                                  setState(() {
                                    if (selected) {
                                      _selectedColors.remove(color);
                                    } else {
                                      _selectedColors.add(color);
                                    }
                                    _clearPending();
                                  });
                                },
                              ),
                            );
                          }).toList(),
                        );
                      },
                    ),
                  ],
                  if (_betType == 'parity') ...[
                    const SizedBox(height: 16),
                    const Text(
                      '单双下注',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    LayoutBuilder(
                      builder: (context, constraints) {
                        const columns = 2;
                        const spacing = 8.0;
                        final itemWidth =
                            (constraints.maxWidth - spacing * (columns - 1)) /
                                columns;
                        return Wrap(
                          spacing: spacing,
                          runSpacing: spacing,
                          children: _parityOptions.map((parity) {
                            final selected = _selectedParity.contains(parity);
                            return SizedBox(
                              width: itemWidth,
                              child: FilterChip(
                                label: Center(child: Text(parity)),
                                selected: selected,
                                showCheckmark: false,
                                padding: EdgeInsets.zero,
                                onSelected: (_) {
                                  setState(() {
                                    if (selected) {
                                      _selectedParity.remove(parity);
                                    } else {
                                      _selectedParity.add(parity);
                                    }
                                    _clearPending();
                                  });
                                },
                              ),
                            );
                          }).toList(),
                        );
                      },
                    ),
                  ],
                  const SizedBox(height: 16),
                  const Text(
                    '赔率与金额',
                    style: TextStyle(fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 8),
                  if (_betType == 'number') ...[
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          '号码下注金额',
                          style: TextStyle(fontWeight: FontWeight.bold),
                        ),
                        const SizedBox(height: 8),
                        if (_selectedNumbers.isEmpty)
                          Text(
                            '请选择号码后设置金额',
                            style: TextStyle(color: Colors.grey.shade600),
                          )
                        else
                          Column(
                            children: (() {
                              final numbers = _selectedNumbers.toList()..sort();
                              return numbers.map((number) {
                                final controller =
                                    _numberStakeControllers[number] ??
                                        TextEditingController(text: '');
                                _numberStakeControllers[number] = controller;
                                return Padding(
                                  padding: const EdgeInsets.only(bottom: 8),
                                  child: Row(
                                    children: [
                                      _Ball(
                                        number: number.toString(),
                                        color: ballColor(number.toString()),
                                        size: 26,
                                        fontSize: 11,
                                      ),
                                      const SizedBox(width: 8),
                                      Expanded(
                                        child: TextField(
                                          controller: controller,
                                          onChanged: (_) => setState(_clearPending),
                                          keyboardType:
                                              const TextInputType.numberWithOptions(
                                                decimal: true,
                                              ),
                                          decoration: const InputDecoration(
                                            labelText: '金额',
                                            border: OutlineInputBorder(),
                                          ),
                                        ),
                                      ),
                                    ],
                                  ),
                                );
                              }).toList();
                            })(),
                          ),
                        const SizedBox(height: 12),
                        _buildOddsAndBettorRow(
                          _numberOddsController,
                          '号码赔率',
                        ),
                      ],
                    ),
                  ],
                  if (_betType == 'zodiac') ...[
                    TextField(
                      controller: _stakeCommonController,
                      onChanged: (_) => setState(_clearPending),
                      keyboardType:
                          const TextInputType.numberWithOptions(decimal: true),
                      decoration: const InputDecoration(
                        labelText: '下注金额',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 8),
                    _buildOddsAndBettorRow(
                      _zodiacOddsController,
                      '生肖赔率',
                    ),
                  ],
                  if (_betType == 'color') ...[
                    TextField(
                      controller: _stakeCommonController,
                      onChanged: (_) => setState(_clearPending),
                      keyboardType:
                          const TextInputType.numberWithOptions(decimal: true),
                      decoration: const InputDecoration(
                        labelText: '下注金额',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 8),
                    _buildOddsAndBettorRow(
                      _colorOddsController,
                      '波色赔率',
                    ),
                  ],
                  if (_betType == 'parity') ...[
                    TextField(
                      controller: _stakeCommonController,
                      onChanged: (_) => setState(_clearPending),
                      keyboardType:
                          const TextInputType.numberWithOptions(decimal: true),
                      decoration: const InputDecoration(
                        labelText: '下注金额',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 8),
                    _buildOddsAndBettorRow(
                      _parityOddsController,
                      '单双赔率',
                    ),
                  ],
                  const SizedBox(height: 12),
                  Builder(builder: (context) {
                    final stakeSpecial =
                        double.tryParse(_stakeSpecialController.text.trim()) ?? 0;
                    final stakeCommon =
                        double.tryParse(_stakeCommonController.text.trim()) ?? 0;
                    final oddsNumber =
                        double.tryParse(_numberOddsController.text.trim()) ?? 0;
                    final oddsZodiac =
                        double.tryParse(_zodiacOddsController.text.trim()) ?? 0;
                    final oddsColor =
                        double.tryParse(_colorOddsController.text.trim()) ?? 0;
                    final oddsParity =
                        double.tryParse(_parityOddsController.text.trim()) ?? 0;

                    double win = 0;
                    double lose = 0;
                    if (_betType == 'number') {
                      final numberStakes = _collectNumberStakes();
                      final totalNumberStake =
                          numberStakes.values.fold(0.0, (sum, value) => sum + value);
                      final maxStake = numberStakes.isEmpty
                          ? 0
                          : numberStakes.values.reduce(
                              (value, element) => value > element ? value : element);
                      win = maxStake * oddsNumber - totalNumberStake;
                      lose = -totalNumberStake;
                    } else if (_betType == 'zodiac') {
                      win = stakeCommon * oddsZodiac - stakeCommon;
                      lose = -stakeCommon;
                    } else if (_betType == 'color') {
                      win = stakeCommon * oddsColor - stakeCommon;
                      lose = -stakeCommon;
                    } else if (_betType == 'parity') {
                      win = stakeCommon * oddsParity - stakeCommon;
                      lose = -stakeCommon;
                    }

                    return Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: Colors.white,
                        borderRadius: BorderRadius.circular(12),
                        boxShadow: const [
                          BoxShadow(
                            color: Color(0x12000000),
                            blurRadius: 6,
                            offset: Offset(0, 2),
                          ),
                        ],
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text(
                            '预估盈亏',
                            style: TextStyle(fontWeight: FontWeight.bold),
                          ),
                          const SizedBox(height: 6),
                          Text('命中：${_formatYuan(win)}'),
                          Text('未中：${_formatYuan(lose)}'),
                        ],
                      ),
                    );
                  }),
                  const SizedBox(height: 16),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton(
                          onPressed: _settling
                              ? null
                              : activationValid
                                  ? _submitBet
                                  : _promptActivation,
                          child: const Text('提交下注'),
                        ),
                      ),
                    ],
                  ),
                  if (_statusMessage != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 12),
                      child: Text(
                        _statusMessage!,
                        style: TextStyle(color: Colors.red.shade400),
                      ),
                    ),
                  const SizedBox(height: 16),
                  Row(
                    children: [
                      const Expanded(
                        child: Text(
                          '已提交下注',
                          style: TextStyle(fontWeight: FontWeight.bold),
                        ),
                      ),
                      IconButton(
                        onPressed: _loadingBets ? null : _loadManualBets,
                        icon: const Icon(Icons.refresh),
                      ),
                    ],
                  ),
                  if (_loadingBets)
                    const Padding(
                      padding: EdgeInsets.symmetric(vertical: 12),
                      child: Center(child: CircularProgressIndicator()),
                    )
                  else if (_manualBets.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      child: Text(
                        '暂无记录',
                        style: TextStyle(color: Colors.grey.shade600),
                      ),
                    )
                  else
                    Builder(
                      builder: (context) {
                        final grouped =
                            <String, List<Map<String, dynamic>>>{};
                        final orderedPeriods = <String>[];
                        for (final item in _manualBets) {
                          final period = item['period']?.toString() ?? '';
                          if (!grouped.containsKey(period)) {
                            grouped[period] = [];
                            orderedPeriods.add(period);
                          }
                          grouped[period]!.add(item);
                        }

                        final visiblePeriods = _showAllManualBetPeriods
                            ? orderedPeriods
                            : orderedPeriods.take(2).toList();

                        return Column(
                          children: [
                            ...visiblePeriods.map((period) {
                              final items = grouped[period] ?? [];
                              return Container(
                                margin: const EdgeInsets.only(bottom: 12),
                                padding: const EdgeInsets.all(12),
                                decoration: BoxDecoration(
                                  color: const Color(0xFFF7FAF9),
                                  borderRadius: BorderRadius.circular(16),
                                  border: Border.all(
                                    color: const Color(0xFFE2E8F0),
                                  ),
                                ),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Row(
                                      children: [
                                        Expanded(
                                          child: Text(
                                            '期号：$period',
                                            style: const TextStyle(
                                              fontSize: 14,
                                              fontWeight: FontWeight.w600,
                                            ),
                                          ),
                                        ),
                                        Text(
                                          '共${items.length}条',
                                          style: TextStyle(
                                            fontSize: 12,
                                            color: Colors.grey.shade600,
                                          ),
                                        ),
                                      ],
                                    ),
                                    const SizedBox(height: 8),
                                    Column(
                                      children: items
                                          .map(_buildManualBetItem)
                                          .toList(),
                                    ),
                                  ],
                                ),
                              );
                            }).toList(),
                            if (!_showAllManualBetPeriods &&
                                orderedPeriods.length > 2)
                              TextButton(
                                onPressed: () {
                                  setState(() {
                                    _showAllManualBetPeriods = true;
                                  });
                                },
                                child: const Text('显示更多'),
                              ),
                            if (_showAllManualBetPeriods &&
                                orderedPeriods.length > 2)
                              TextButton(
                                onPressed: () {
                                  setState(() {
                                    _showAllManualBetPeriods = false;
                                  });
                                },
                                child: const Text('收起'),
                              ),
                          ],
                        );
                      },
                    ),
                  if (_outcomes.isNotEmpty) ...[
                    const SizedBox(height: 16),
                    const Text(
                      '结算结果',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    ..._outcomes.map(
                      (item) => Container(
                        margin: const EdgeInsets.only(bottom: 8),
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: Colors.white,
                          borderRadius: BorderRadius.circular(12),
                          boxShadow: const [
                            BoxShadow(
                              color: Color(0x12000000),
                              blurRadius: 6,
                              offset: Offset(0, 2),
                            ),
                          ],
                        ),
                        child: Row(
                          children: [
                            Expanded(
                              child: Text(
                                '${item.label} 赔率 ${item.odds.toStringAsFixed(0)}',
                                style: const TextStyle(fontWeight: FontWeight.w600),
                              ),
                            ),
                            Text(
                              item.win ? '中奖' : '未中',
                              style: TextStyle(
                                color: item.win ? const Color(0xFF2563EB) : Colors.redAccent,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(width: 12),
                            Text(
                              _formatYuan(item.profit),
                              style: TextStyle(
                                color: item.profit >= 0
                                    ? const Color(0xFF2563EB)
                                    : Colors.redAccent,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 8),
                    _buildBetSummaryWrap(
                      stake: _totalStake,
                      profit: _totalProfit,
                    ),
                  ],
                  const SizedBox(height: 24),
                ],
              ),
            ),
    );
  }
}

class RecordsScreen extends StatefulWidget {
  const RecordsScreen({super.key});

  @override
  State<RecordsScreen> createState() => _RecordsScreenState();
}

class _RecordsScreenState extends State<RecordsScreen> {
  List<DrawRecord> _records = [];
  List<DrawRecord> _allRecords = [];
  String _region = 'macau';
  bool _loading = false;
  bool _updatingDraws = false;
  bool _nextDrawLoading = false;
  String? _nextDrawTime;
  Timer? _countdownTimer;
  Timer? _recordsRefreshTimer;
  final TextEditingController _yearController = TextEditingController();
  final TextEditingController _monthController = TextEditingController();
  final TextEditingController _periodController = TextEditingController();
  final TextEditingController _specialNumberController = TextEditingController();
  final TextEditingController _specialZodiacController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _yearController.text = DateTime.now().year.toString();
    _restoreRegionAndLoad();
    _countdownTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) {
        setState(() {});
      }
    });
    _recordsRefreshTimer = Timer.periodic(const Duration(minutes: 2), (_) {
      _refreshRecordsSilently();
    });
  }

  Future<void> _restoreRegionAndLoad() async {
    final savedRegion = await _loadLastRegion();
    if (!mounted) return;
    setState(() => _region = savedRegion);
    _fetch();
    _fetchNextDrawTime();
  }

  @override
  void dispose() {
    _countdownTimer?.cancel();
    _recordsRefreshTimer?.cancel();
    _yearController.dispose();
    _monthController.dispose();
    _periodController.dispose();
    _specialNumberController.dispose();
    _specialZodiacController.dispose();
    super.dispose();
  }

  Future<void> _fetch({bool showLoading = true}) async {
    if (showLoading) {
      setState(() => _loading = true);
    }
    try {
      final year = _yearController.text.trim().isEmpty
          ? DateTime.now().year.toString()
          : _yearController.text.trim();
      final raw = await ApiClient.instance.draws(region: _region, year: year);
      final records = raw
          .map((item) => DrawRecord.fromJson(item as Map<String, dynamic>))
          .toList();
      setState(() {
        _allRecords = records;
      });
      _applyFilters();
    } catch (_) {
      _showMessage('获取开奖数据失败');
    } finally {
      if (mounted && showLoading) {
        setState(() => _loading = false);
      }
    }
  }

  Future<void> _refreshRecordsSilently() async {
    if (!mounted || _loading || _updatingDraws) return;
    await _fetch(showLoading: false);
    await _fetchNextDrawTime();
  }

  Future<void> _fetchNextDrawTime() async {
    if (_region != 'hk') {
      if (!mounted) return;
      setState(() {
        _nextDrawTime = null;
        _nextDrawLoading = false;
      });
      return;
    }

    setState(() {
      _nextDrawLoading = true;
    });

    try {
      final data = await ApiClient.instance.nextDrawTime(region: 'hk');
      final raw = data['next_time']?.toString().trim();
      final normalized = _normalizeDateTimeString(raw);
      if (!mounted) return;
      setState(() {
        _nextDrawTime = normalized ?? raw;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _nextDrawTime = _formatDateTime(_nextHkDrawTime());
      });
    } finally {
      if (!mounted) return;
      setState(() {
        _nextDrawLoading = false;
      });
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  String _formatDateTime(DateTime value) {
    final year = value.year.toString().padLeft(4, '0');
    final month = value.month.toString().padLeft(2, '0');
    final day = value.day.toString().padLeft(2, '0');
    final hour = value.hour.toString().padLeft(2, '0');
    final minute = value.minute.toString().padLeft(2, '0');
    return '$year-$month-$day $hour:$minute';
  }

  String? _normalizeDateTimeString(String? raw) {
    if (raw == null || raw.isEmpty) return null;
    final match = RegExp(
      r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2})',
    ).firstMatch(raw);
    if (match == null) return null;
    final year = int.tryParse(match.group(1) ?? '');
    final month = int.tryParse(match.group(2) ?? '');
    final day = int.tryParse(match.group(3) ?? '');
    final hour = int.tryParse(match.group(4) ?? '');
    final minute = int.tryParse(match.group(5) ?? '');
    if (year == null ||
        month == null ||
        day == null ||
        hour == null ||
        minute == null) {
      return null;
    }
    final value = DateTime(year, month, day, hour, minute);
    return _formatDateTime(value);
  }

  DateTime? _parseDateTimeString(String? raw) {
    final normalized = _normalizeDateTimeString(raw);
    if (normalized == null) return null;
    final match = RegExp(
      r'(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})',
    ).firstMatch(normalized);
    if (match == null) return null;
    final year = int.tryParse(match.group(1) ?? '');
    final month = int.tryParse(match.group(2) ?? '');
    final day = int.tryParse(match.group(3) ?? '');
    final hour = int.tryParse(match.group(4) ?? '');
    final minute = int.tryParse(match.group(5) ?? '');
    if (year == null ||
        month == null ||
        day == null ||
        hour == null ||
        minute == null) {
      return null;
    }
    return DateTime(year, month, day, hour, minute);
  }

  DateTime _nextMacauDrawTime() {
    final now = DateTime.now();
    final todayDraw = DateTime(now.year, now.month, now.day, 21, 32);
    if (now.isBefore(todayDraw)) {
      return todayDraw;
    }
    return todayDraw.add(const Duration(days: 1));
  }

  DateTime _nextHkDrawTime() {
    final now = DateTime.now();
    const drawHour = 21;
    const drawMinute = 32;
    final todayDraw = DateTime(now.year, now.month, now.day, drawHour, drawMinute);

    const drawDays = <int>{2, 4, 6}; // Tue, Thu, Sat
    final todayIsDrawDay = drawDays.contains(now.weekday);
    if (todayIsDrawDay && now.isBefore(todayDraw)) {
      return todayDraw;
    }

    for (var i = 1; i <= 7; i++) {
      final candidate = now.add(Duration(days: i));
      if (drawDays.contains(candidate.weekday)) {
        return DateTime(
          candidate.year,
          candidate.month,
          candidate.day,
          drawHour,
          drawMinute,
        );
      }
    }
    return todayDraw.add(const Duration(days: 2));
  }

  void _applyFilters() {
    final month = _monthController.text.trim();
    final period = _periodController.text.trim();
    final specialNo = _specialNumberController.text.trim();
    final specialZodiac = _specialZodiacController.text.trim();

    var filtered = List<DrawRecord>.from(_allRecords);
    if (month.isNotEmpty) {
      filtered = filtered.where((record) {
        final parts = record.date.split('-');
        if (parts.length < 2) return false;
        final value = parts[1].padLeft(2, '0');
        final target = month.padLeft(2, '0');
        return value == target;
      }).toList();
    }
    if (period.isNotEmpty) {
      filtered =
          filtered.where((record) => record.id.contains(period)).toList();
    }
    if (specialNo.isNotEmpty) {
      filtered = filtered
          .where((record) => record.specialNumber.contains(specialNo))
          .toList();
    }
    if (specialZodiac.isNotEmpty) {
      filtered = filtered.where((record) {
        final rawZodiacs = record.rawZodiacs;
        final zodiac = rawZodiacs.length > record.normalNumbers.length
            ? rawZodiacs[record.normalNumbers.length]
            : record.specialZodiac;
        return zodiac.contains(specialZodiac);
      }).toList();
    }

    setState(() => _records = filtered);
  }

  void _resetFilters() {
    _monthController.clear();
    _periodController.clear();
    _specialNumberController.clear();
    _specialZodiacController.clear();
    _applyFilters();
  }

  Future<void> _updateDrawData() async {
    setState(() => _updatingDraws = true);
    try {
      final res = await ApiClient.instance.updateDrawData(region: _region);
      final message = res['message']?.toString() ?? '';
      if (res['success'] == true) {
        _showMessage(message.isEmpty ? '开奖记录更新完成' : message);
        await _fetch(showLoading: false);
        await _fetchNextDrawTime();
      } else {
        _showMessage(message.isEmpty ? '更新开奖记录失败' : message);
      }
    } catch (e) {
      _showMessage('更新开奖记录失败: $e');
    } finally {
      if (mounted) {
        setState(() => _updatingDraws = false);
      }
    }
  }

  Widget _buildHeaderRegionButton(String value, String label) {
    final selected = _region == value;
    final color = value == 'macau'
        ? const Color(0xFF2563EB)
        : const Color(0xFFB91C1C);
    return Expanded(
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () async {
          if (_region == value) return;
          await _saveLastRegion(value);
          if (!mounted) return;
          setState(() => _region = value);
          _fetch(showLoading: false);
          _fetchNextDrawTime();
        },
        child: Container(
          height: 48,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected ? color : color.withOpacity(0.10),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: selected ? color : color.withOpacity(0.35),
              width: selected ? 1.5 : 1,
            ),
            boxShadow: const [
              BoxShadow(
                color: Color(0x0F000000),
                blurRadius: 8,
                offset: Offset(0, 2),
              ),
            ],
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                label,
                style: TextStyle(
                  color: selected ? Colors.white : color,
                  fontWeight: FontWeight.w800,
                  fontSize: 16,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  String _nextDrawHintText() {
    final countdown = _nextDrawCountdownText();
    if (_region == 'macau') {
      return '下期开奖时间：${_formatDateTime(_nextMacauDrawTime())}  $countdown';
    }
    if (_nextDrawLoading) {
      return '下期开奖时间：加载中...';
    }
    if (_nextDrawTime != null && _nextDrawTime!.isNotEmpty) {
      return '下期开奖时间：$_nextDrawTime  $countdown';
    }
    return '下期开奖时间：${_formatDateTime(_nextHkDrawTime())}  $countdown';
  }

  DateTime _nextDrawTargetTime() {
    if (_region == 'macau') {
      return _nextMacauDrawTime();
    }
    return _parseDateTimeString(_nextDrawTime) ?? _nextHkDrawTime();
  }

  String _nextDrawCountdownText() {
    final target = _nextDrawTargetTime();
    final diff = target.difference(DateTime.now());
    if (diff.inSeconds <= 0) {
      return '开奖倒计时：即将开奖';
    }
    final days = diff.inDays;
    final hours = diff.inHours % 24;
    final minutes = diff.inMinutes % 60;
    final seconds = diff.inSeconds % 60;
    final timeText =
        "${hours.toString().padLeft(2, '0')}:"
        "${minutes.toString().padLeft(2, '0')}:"
        "${seconds.toString().padLeft(2, '0')}";
    if (days > 0) {
      return '开奖倒计时：$days天 $timeText';
    }
    return '开奖倒计时：$timeText';
  }

  Widget _buildRegionSelector() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _buildHeaderRegionButton('macau', '澳门'),
              const SizedBox(width: 10),
              _buildHeaderRegionButton('hk', '香港'),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _openSearchSheet() async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) {
        return Container(
          padding: EdgeInsets.only(
            left: 16,
            right: 16,
            top: 16,
            bottom: MediaQuery.of(context).viewInsets.bottom + 16,
          ),
          decoration: const BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
          ),
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 36,
                  height: 4,
                  margin: const EdgeInsets.only(bottom: 12),
                  decoration: BoxDecoration(
                    color: Colors.grey.shade300,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
                Row(
                  children: [
                    const Expanded(
                      child: Text(
                        '筛选开奖',
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                    IconButton(
                      onPressed: () => Navigator.of(context).pop(),
                      icon: const Icon(Icons.close),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _yearController,
                        decoration: const InputDecoration(
                          labelText: '年份',
                        ),
                        keyboardType: TextInputType.number,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: TextField(
                        controller: _monthController,
                        decoration: const InputDecoration(
                          labelText: '月份',
                        ),
                        keyboardType: TextInputType.number,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: TextField(
                        controller: _periodController,
                        decoration: const InputDecoration(
                          labelText: '期号',
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _specialNumberController,
                        decoration: const InputDecoration(
                          labelText: '特码号码',
                        ),
                        keyboardType: TextInputType.number,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: TextField(
                        controller: _specialZodiacController,
                        decoration: const InputDecoration(
                          labelText: '特码生肖',
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: ElevatedButton.icon(
                        onPressed: _loading
                            ? null
                            : () async {
                                Navigator.of(context).pop();
                                await _fetch();
                              },
                        icon: const Icon(Icons.search),
                        label: const Text('搜索'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    OutlinedButton(
                      onPressed: () {
                        _resetFilters();
                        Navigator.of(context).pop();
                      },
                      child: const Text('重置'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('开奖记录')),
      body: Column(
        children: [
          _buildRegionSelector(),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 10),
            child: Row(
              children: [
                const Icon(
                  Icons.schedule,
                  size: 14,
                  color: Color(0xFF6B7280),
                ),
                const SizedBox(width: 4),
                Expanded(
                  child: Text(
                    _nextDrawHintText(),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: Color(0xFF6B7280),
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                Expanded(
                  child: ElevatedButton.icon(
                    onPressed: _openSearchSheet,
                    icon: const Icon(Icons.search),
                    label: const Text('筛选搜索'),
                  ),
                ),
                const SizedBox(width: 12),
                OutlinedButton.icon(
                  onPressed: (_loading || _updatingDraws)
                      ? null
                      : _updateDrawData,
                  icon: _updatingDraws
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.refresh),
                  label: Text(_updatingDraws ? '更新中' : '更新开奖'),
                ),
              ],
            ),
          ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : ListView.separated(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    itemCount: _records.length,
                    separatorBuilder: (_, __) => const SizedBox(height: 12),
                    itemBuilder: (context, index) {
                      final record = _records[index];
                      final rawZodiacs = record.rawZodiacs;
                      final normalZodiacs = rawZodiacs.length >=
                              record.normalNumbers.length
                          ? rawZodiacs
                              .take(record.normalNumbers.length)
                              .toList()
                          : List.filled(record.normalNumbers.length, '');
                      final specialZodiac = rawZodiacs.length >
                              record.normalNumbers.length
                          ? rawZodiacs[record.normalNumbers.length]
                          : record.specialZodiac;
                      return Card(
                        elevation: 2,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(16),
                        ),
                        child: Padding(
                          padding: const EdgeInsets.all(16),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                '期号：${record.id}  开奖时间：${record.date}',
                                style: const TextStyle(
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              const SizedBox(height: 12),
                              Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  const Text(
                                    '平码：',
                                    style: TextStyle(fontWeight: FontWeight.w600),
                                  ),
                                  const SizedBox(height: 8),
                                  _buildNumberGrid(
                                    numbers: record.normalNumbers,
                                    zodiacs: normalZodiacs,
                                  ),
                                  const SizedBox(height: 12),
              Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  const Text(
                    '特码：',
                                        style: TextStyle(fontWeight: FontWeight.w600),
                                      ),
                                      const SizedBox(width: 8),
                                      _NumberZodiacTile(
                                        number: record.specialNumber,
                                        zodiac: specialZodiac,
                                        color: ballColor(record.specialNumber),
                                        outlined: true,
                                        highlight: true,
                  ),
                ],
              ),
            ],
          ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}

class _NumberZodiacTile extends StatelessWidget {
  const _NumberZodiacTile({
    required this.number,
    required this.zodiac,
    required this.color,
    this.outlined = false,
    this.highlight = false,
    this.ballSize = 46,
    this.numberFontSize = 16,
    this.zodiacFontSize = 12,
    this.gap = 4,
  });

  final String number;
  final String zodiac;
  final Color color;
  final bool outlined;
  final bool highlight;
  final double ballSize;
  final double numberFontSize;
  final double zodiacFontSize;
  final double gap;

  @override
  Widget build(BuildContext context) {
    final ball = _Ball(
      number: number,
      color: color,
      outlined: outlined,
      size: ballSize,
      fontSize: numberFontSize,
    );
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        highlight
            ? Container(
                padding: const EdgeInsets.all(2),
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  boxShadow: [
                    BoxShadow(
                      color: color.withOpacity(0.35),
                      blurRadius: 12,
                      spreadRadius: 2,
                    ),
                  ],
                ),
                child: Stack(
                  clipBehavior: Clip.none,
                  children: [
                    ball,
                    Positioned(
                      top: -6,
                      right: -6,
                      child: Container(
                        width: 18,
                        height: 18,
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                          color: color,
                          shape: BoxShape.circle,
                          border: Border.all(color: Colors.white, width: 2),
                        ),
                        child: const Text(
                          '特',
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 10,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              )
            : ball,
        SizedBox(height: gap),
        Text(
          zodiac,
          style: TextStyle(fontSize: zodiacFontSize, color: Colors.grey.shade700),
        ),
      ],
    );
  }
}

Widget _buildNumberGrid({
  required List<String> numbers,
  required List<String> zodiacs,
  double ballSize = 46,
  double numberFontSize = 16,
  double zodiacFontSize = 12,
  double gap = 4,
  double childAspectRatio = 0.75,
}) {
  return GridView.builder(
    shrinkWrap: true,
    physics: const NeverScrollableScrollPhysics(),
    gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
      crossAxisCount: 6,
      crossAxisSpacing: 8,
      mainAxisSpacing: 8,
      childAspectRatio: childAspectRatio,
    ),
    itemCount: numbers.length,
    itemBuilder: (context, index) {
      final zodiac = index < zodiacs.length ? zodiacs[index] : '';
      final number = numbers[index];
      return _NumberZodiacTile(
        number: number,
        zodiac: zodiac,
        color: ballColor(number),
        ballSize: ballSize,
        numberFontSize: numberFontSize,
        zodiacFontSize: zodiacFontSize,
        gap: gap,
      );
    },
  );
}

class _Ball extends StatelessWidget {
  const _Ball({
    required this.number,
    required this.color,
    this.outlined = false,
    this.size = 46,
    this.fontSize = 16,
  });

  final String number;
  final Color color;
  final bool outlined;
  final double size;
  final double fontSize;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: outlined ? Colors.white : color,
        border: Border.all(color: color, width: 2),
      ),
      child: Center(
        child: Text(
          number,
          textAlign: TextAlign.center,
          strutStyle: StrutStyle(
            fontSize: fontSize,
            height: 1,
            leading: 0,
            forceStrutHeight: true,
          ),
          style: TextStyle(
            color: outlined ? color : Colors.white,
            fontWeight: FontWeight.bold,
            fontSize: fontSize,
            height: 1,
            leadingDistribution: TextLeadingDistribution.even,
          ),
        ),
      ),
    );
  }
}
class PredictScreen extends StatefulWidget {
  const PredictScreen({super.key, required this.appState});

  final AppState appState;

  @override
  State<PredictScreen> createState() => _PredictScreenState();
}

class _PredictScreenState extends State<PredictScreen> {
  String _region = 'macau';
  String _strategy = 'ml';
  bool _loading = false;
  String _aiText = '';
  Map<String, dynamic>? _result;
  List<String> _normalZodiacs = [];
  String _specialZodiac = '';
  bool _loadingRecords = false;
  List<PredictionItem> _predictionRecords = [];
  List<Map<String, dynamic>> _regionSummaries = [];
  bool _showAllPredictionPeriods = false;
  final Map<int, List<String>> _recordNormalZodiacs = {};
  final Map<int, String> _recordSpecialZodiacs = {};
  StreamSubscription<Map<String, dynamic>>? _aiSubscription;

  bool get _showNormalNumbers =>
      widget.appState.user?.showNormalNumbers ?? false;

  final Map<String, String> _strategyLabels = const {
    'ml': '机器学习',
    'markov': '马尔科夫',
    'hybrid': '综合',
    'balanced': '均衡',
    'hot': '热门',
    'cold': '冷门',
    'trend': '走势',
    'ai': 'AI智能',
  };

  final Map<String, IconData> _strategyIcons = const {
    'ml': Icons.memory,
    'markov': Icons.account_tree,
    'hybrid': Icons.hub,
    'balanced': Icons.balance,
    'hot': Icons.local_fire_department,
    'cold': Icons.ac_unit,
    'trend': Icons.trending_up,
    'ai': Icons.auto_awesome,
  };

  LinearGradient? _strategyGradient(String key) {
    switch (key) {
      case 'hot':
        return const LinearGradient(
          colors: [Color(0xFFFF6A00), Color(0xFFEE0979)],
        );
      case 'cold':
        return const LinearGradient(
          colors: [Color(0xFF36D1DC), Color(0xFF5B86E5)],
        );
      case 'trend':
        return const LinearGradient(
          colors: [Color(0xFF11998E), Color(0xFF38EF7D)],
        );
      case 'hybrid':
        return const LinearGradient(
          colors: [Color(0xFF7F00FF), Color(0xFFE100FF)],
        );
      case 'balanced':
        return const LinearGradient(
          colors: [Color(0xFFFFC107), Color(0xFFFD7E14)],
        );
      case 'markov':
        return const LinearGradient(
          colors: [Color(0xFF2563EB), Color(0xFFF4B547)],
        );
      case 'ml':
        return const LinearGradient(
          colors: [Color(0xFFB45309), Color(0xFFF4B547)],
        );
      case 'ai':
        return const LinearGradient(
          colors: [Color(0xFF17A2B8), Color(0xFF6F42C1)],
        );
    }
    return null;
  }

  Widget _buildStrategyChip(String key, String label) {
    final selected = _strategy == key;
    final gradient = _strategyGradient(key);
    final accentColor = gradient?.colors.first ?? const Color(0xFFB91C1C);
    final activationValid = widget.appState.activationValid;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () async {
          if (_loading) return;
          if (!activationValid) {
            await _promptActivation();
            return;
          }
          setState(() => _strategy = key);
          _handlePredict();
        },
        child: Opacity(
          opacity: activationValid ? 1 : 0.5,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 160),
            height: 42,
            padding: const EdgeInsets.symmetric(horizontal: 8),
            decoration: BoxDecoration(
              gradient: selected ? gradient : null,
              color: selected ? null : const Color(0xFFF7FAFC),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: selected ? Colors.transparent : const Color(0xFFE2E8F0),
              ),
              boxShadow: selected
                  ? [
                      BoxShadow(
                        color: accentColor.withOpacity(0.22),
                        blurRadius: 12,
                        offset: const Offset(0, 6),
                      ),
                    ]
                  : [],
            ),
            child: Row(
              children: [
                Container(
                  width: 24,
                  height: 24,
                  decoration: BoxDecoration(
                    color: selected
                        ? Colors.white.withOpacity(0.18)
                        : accentColor.withOpacity(0.10),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Icon(
                    _strategyIcons[key] ?? Icons.auto_graph,
                    size: 15,
                    color: selected ? Colors.white : accentColor,
                  ),
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      color: selected ? Colors.white : const Color(0xFF1F2937),
                      fontSize: 12,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildPredictionRegionButton(String value, String label) {
    final selected = _region == value;
    final color = value == 'macau'
        ? const Color(0xFF2563EB)
        : const Color(0xFFB91C1C);
    return Expanded(
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () async {
          if (_region == value) return;
          await _saveLastRegion(value);
          if (!mounted) return;
          setState(() {
            _region = value;
            _resetPrediction();
          });
          _loadPredictionRecords();
        },
        child: Container(
          height: 48,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected ? color : color.withOpacity(0.10),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: selected ? color : color.withOpacity(0.35),
              width: selected ? 1.5 : 1,
            ),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                label,
                style: TextStyle(
                  color: selected ? Colors.white : color,
                  fontWeight: FontWeight.w800,
                  fontSize: 16,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  void initState() {
    super.initState();
    _restoreRegionAndLoad();
  }

  Future<void> _restoreRegionAndLoad() async {
    final savedRegion = await _loadLastRegion();
    if (!mounted) return;
    setState(() => _region = savedRegion);
    _loadPredictionRecords();
  }

  @override
  void dispose() {
    _aiSubscription?.cancel();
    super.dispose();
  }

  void _resetPrediction() {
    _result = null;
    _aiText = '';
    _normalZodiacs = [];
    _specialZodiac = '';
  }

  void _upsertPredictionRecordFromResult({
    required Map<String, dynamic> result,
    required List<String> normalNumbers,
    required List<String> normalZodiacs,
    required String specialNumber,
    required String specialZodiac,
  }) {
    final strategy = result['strategy']?.toString().trim().isNotEmpty == true
        ? result['strategy']!.toString().trim()
        : _strategy;
    final period = result['period']?.toString().trim() ?? '';
    if (period.isEmpty) return;

    final createdAtRaw = result['created_at']?.toString();
    final createdAt = createdAtRaw != null && createdAtRaw.isNotEmpty
        ? DateTime.tryParse(createdAtRaw)
        : DateTime.now();

    final item = PredictionItem(
      id: (result['prediction_id'] as num?)?.toInt() ??
          DateTime.now().microsecondsSinceEpoch,
      region: _region,
      strategy: strategy,
      period: period,
      normalNumbers: normalNumbers,
      normalZodiacs: normalZodiacs,
      specialNumber: specialNumber,
      specialZodiac: specialZodiac,
      actualSpecialNumber: '',
      actualSpecialZodiac: '',
      result: 'pending',
      createdAt: createdAt,
    );

    setState(() {
      final next = List<PredictionItem>.from(_predictionRecords);
      final existingIndex = next.indexWhere(
        (record) =>
            record.region == item.region &&
            record.period == item.period &&
            record.strategy == item.strategy,
      );
      if (existingIndex >= 0) {
        next[existingIndex] = item;
      } else {
        next.insert(0, item);
      }
      next.sort((a, b) {
        final aTime = a.createdAt?.millisecondsSinceEpoch ?? 0;
        final bTime = b.createdAt?.millisecondsSinceEpoch ?? 0;
        return bTime.compareTo(aTime);
      });
      _predictionRecords = next;
      _recordNormalZodiacs[item.id] = normalZodiacs;
      _recordSpecialZodiacs[item.id] = specialZodiac;
    });
  }

  Future<void> _updateZodiacs(List<String> numbers) async {
    if (numbers.isEmpty) return;
    try {
      // 生肖映射必须按当年规则计算，不能复用预测接口的全量历史 year=all。
      final res = await ApiClient.instance.getZodiacs(
        numbers: numbers,
        region: _region,
        year: _currentYear,
      );
      final normal = (res['normal_zodiacs'] as List<dynamic>? ?? [])
          .map((value) => value.toString())
          .toList();
      final special = res['special_zodiac']?.toString() ?? '';
      if (!mounted) return;
      setState(() {
        _normalZodiacs = normal;
        _specialZodiac = special;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _normalZodiacs = [];
        _specialZodiac = '';
      });
    }
  }

  Future<void> _handlePredict() async {
    if (!widget.appState.activationValid) {
      await _promptActivation();
      return;
    }
    setState(() {
      _loading = true;
      _resetPrediction();
    });

    if (_strategy == 'ai') {
      _aiSubscription?.cancel();
      _aiSubscription = ApiClient.instance
          .predictAiStream(region: _region, year: _predictionYear)
          .listen((event) async {
        if (!mounted) return;
        if (event['type'] == 'content') {
          setState(() {
            _aiText += event['content']?.toString() ?? '';
          });
        } else if (event['type'] == 'done' ||
            event.containsKey('normal') ||
            event.containsKey('special')) {
          final normal = _uniqueNumbers(
            (event['normal'] as List<dynamic>? ?? [])
                .map((value) => value.toString())
                .toList(),
          );
          final special = (event['special'] as Map<String, dynamic>? ?? {});
          final specialNumber = special['number']?.toString() ?? '';
          final cleanNormal = _removeSpecialFromNormal(normal, specialNumber);
          final numbers = [...cleanNormal, specialNumber]
              .where((n) => n.isNotEmpty)
              .toList();
          setState(() {
            _result = {
              ...event,
              'normal': cleanNormal,
              'special': special,
            };
            _loading = false;
          });
          await _updateZodiacs(numbers);
          if (!mounted) return;
          _upsertPredictionRecordFromResult(
            result: _result ?? event,
            normalNumbers: cleanNormal,
            normalZodiacs: _normalZodiacs,
            specialNumber: specialNumber,
            specialZodiac: _specialZodiac,
          );
        } else if (event.containsKey('error')) {
          setState(() {
            _loading = false;
          });
          _showMessage(event['error']?.toString() ?? 'AI预测失败');
        } else if (event['type'] == 'error') {
          setState(() {
            _loading = false;
          });
          _showMessage(event['error']?.toString() ?? 'AI预测失败');
        }
      }, onError: (e) {
        if (!mounted) return;
        setState(() => _loading = false);
        _showMessage('AI预测服务暂时不可用，请稍后重试');
      }, onDone: () {
        if (!mounted) return;
        setState(() => _loading = false);
      });
      return;
    }

    await _runPredictOnce(_strategy);
    if (mounted) {
      setState(() => _loading = false);
    }
  }

    Future<bool> _runPredictOnce(String strategy) async {
      try {
        final res = await ApiClient.instance.predict(
          region: _region,
          strategy: strategy,
          year: _predictionYear,
        );
        if (res['success'] == false || res.containsKey('error')) {
          final message = res['message']?.toString() ??
              res['error']?.toString() ??
              '预测失败';
          if (!mounted) return false;
          _showMessage(message);
          return false;
        }
        final normal = _uniqueNumbers(
          (res['normal'] as List<dynamic>? ?? [])
              .map((value) => value.toString())
              .toList(),
        );
      final special = res['special'] as Map<String, dynamic>? ?? {};
      final specialNumber = special['number']?.toString() ?? '';
      final cleanNormal = _removeSpecialFromNormal(normal, specialNumber);
      final numbers = [...cleanNormal, specialNumber]
          .where((n) => n.isNotEmpty)
          .toList();
      if (!mounted) return false;
      setState(() {
        _result = {
          ...res,
          'normal': cleanNormal,
          'special': special,
        };
      });
      await _updateZodiacs(numbers);
      if (!mounted) return false;
      _upsertPredictionRecordFromResult(
        result: _result ?? res,
        normalNumbers: cleanNormal,
        normalZodiacs: _normalZodiacs,
        specialNumber: specialNumber,
        specialZodiac: _specialZodiac,
      );
      return true;
    } catch (e) {
      if (!mounted) return false;
      setState(() => _loading = false);
      _showMessage('预测失败: $e');
      return false;
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  Future<void> _promptActivation() async {
    await showActivationDialog(context, widget.appState);
    if (mounted) {
      setState(() {});
    }
  }

  Widget _buildPredictionNumbers() {
    final normal = _uniqueNumbers(
      (_result?['normal'] as List<dynamic>? ?? [])
          .map((value) => value.toString())
          .toList(),
    );
    final specialMap = _result?['special'] as Map<String, dynamic>? ?? {};
    final specialNumber = specialMap['number']?.toString() ?? '';
    final cleanNormal = _removeSpecialFromNormal(normal, specialNumber);
    if (normal.isEmpty && specialNumber.isEmpty) {
      return const Text('暂无预测结果');
    }

    final showReferenceNumbers = _showNormalNumbers;
    final normalZodiacs = _normalZodiacs.length == cleanNormal.length
        ? _normalZodiacs
        : List.filled(cleanNormal.length, '');

    return Wrap(
      alignment: WrapAlignment.center,
      runAlignment: WrapAlignment.center,
      spacing: 8,
      runSpacing: 8,
      children: [
        if (showReferenceNumbers)
          ...cleanNormal.asMap().entries.map(
                (entry) => _NumberZodiacTile(
                  number: entry.value,
                  zodiac: normalZodiacs[entry.key],
                  color: ballColor(entry.value),
                  ballSize: 36,
                  numberFontSize: 13,
                  zodiacFontSize: 11,
                  gap: 3,
                ),
              ),
        if (specialNumber.isNotEmpty)
          _NumberZodiacTile(
            number: specialNumber,
            zodiac: _specialZodiac,
            color: ballColor(specialNumber),
            outlined: true,
            highlight: true,
            ballSize: 36,
            numberFontSize: 13,
            zodiacFontSize: 11,
            gap: 3,
          ),
      ],
    );
  }

  String get _aiMarkdownText {
    final streaming = _aiText.trim();
    if (streaming.isNotEmpty) {
      return streaming;
    }
    return _result?['recommendation_text']?.toString().trim() ?? '';
  }

  String get _resultStrategy {
    return _result?['requested_strategy']?.toString() ??
        _result?['strategy']?.toString() ??
        _strategy;
  }

  Map<String, dynamic> get _resultDisplayCopy {
    final raw = _result?['display_copy'];
    if (raw is Map) {
      return Map<String, dynamic>.from(raw);
    }
    return const <String, dynamic>{};
  }

  String _analysisTitle() {
    final title = _resultDisplayCopy['analysis_title']?.toString().trim() ?? '';
    if (title.isNotEmpty) {
      return title;
    }
    switch (_resultStrategy) {
      case 'ml':
        return '机器学习分析';
      case 'ai':
        return 'AI分析';
      case 'markov':
        return '马尔科夫分析';
      default:
        return '分析说明';
    }
  }

  String _mlRuntimeProfileLabel(String value) {
    switch (value) {
      case 'base':
        return '标准模式';
      case 'compact':
        return '轻量模式';
      case 'deep':
        return '深度模式';
      case 'adaptive':
        return '自动调整';
      case 'recent_bias':
        return '侧重近期走势';
      case 'context_bias':
        return '侧重号码属性';
      case 'recency_trim':
        return '近期简化模式';
      default:
        return value.isEmpty ? '标准模式' : value;
    }
  }

  String _mlFeatureProfileLabel(String value) {
    switch (value) {
      case 'full':
        return '综合参考全部因素';
      case 'compact_structure':
        return '侧重整体结构';
      case 'compact_attributes':
        return '侧重波色生肖单双';
      case 'compact_recency':
        return '侧重近期走势';
      default:
        return value.isEmpty ? '综合参考全部因素' : value;
    }
  }

  String _mlPromotionStrengthLabel(String value) {
    switch (value) {
      case 'hold':
        return '观察中';
      case 'watch':
        return '重点观察';
      case 'promoted':
        return '已提升';
      default:
        return value.isEmpty ? '观察中' : value;
    }
  }

  Widget _buildInsightMetric(String label, String value) {
    return Container(
      width: 140,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.75),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: TextStyle(
              fontSize: 11,
              color: Colors.grey.shade700,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            value,
            style: const TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildMlWeightReasonCard(
    int rank,
    String strategyLabel,
    String weightText,
    String accuracyText,
    String multiplierText,
  ) {
    final palettes = [
      (
        background: const LinearGradient(
          colors: [Color(0xFFFFF8D6), Color(0xFFFFECB0)],
        ),
        border: const Color(0x47C49200),
        badge: const Color(0xFFC69200),
        title: const Color(0xFF7A5600),
        ribbon: const LinearGradient(
          colors: [Color(0xFFC69200), Color(0xFFFFC107)],
        ),
        ribbonTitle: '冠军策略',
        ribbonNote: '当前集成优先级最高',
      ),
      (
        background: const LinearGradient(
          colors: [Color(0xFFF0F4F8), Color(0xFFDFE7EF)],
        ),
        border: const Color(0x42607D8B),
        badge: const Color(0xFF607D8B),
        title: const Color(0xFF38505D),
        ribbon: const LinearGradient(
          colors: [Color(0xFF607D8B), Color(0xFFB0BEC5)],
        ),
        ribbonTitle: '亚军策略',
        ribbonNote: '当前集成优先级第二',
      ),
      (
        background: const LinearGradient(
          colors: [Color(0xFFFFF1E6), Color(0xFFFBDFC6)],
        ),
        border: const Color(0x42BF6622),
        badge: const Color(0xFFBF6622),
        title: const Color(0xFF8A4516),
        ribbon: const LinearGradient(
          colors: [Color(0xFFBF6622), Color(0xFFCD7F32)],
        ),
        ribbonTitle: '季军策略',
        ribbonNote: '当前集成优先级第三',
      ),
    ];
    final paletteIndex = ((rank - 1).clamp(0, palettes.length - 1)) as int;
    final palette = palettes[paletteIndex];
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        gradient: palette.background,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: palette.border),
        boxShadow: [
          BoxShadow(
            color: rank == 1
                ? const Color(0x33C69200)
                : rank == 2
                    ? const Color(0x23607D8B)
                    : const Color(0x23BF6622),
            blurRadius: rank == 1 ? 18 : 14,
            offset: Offset(0, rank == 1 ? 6 : 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            margin: const EdgeInsets.only(bottom: 10),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
              gradient: palette.ribbon,
              borderRadius: BorderRadius.circular(10),
            ),
            child: Row(
              children: [
                Text(
                  palette.ribbonTitle,
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w900,
                    color: Color(0xFFFFFAF0),
                  ),
                ),
                const Spacer(),
                Text(
                  palette.ribbonNote,
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    color: Color(0xFFFFFAF0),
                  ),
                ),
              ],
            ),
          ),
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: palette.badge,
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Text(
                  '#$rank',
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w800,
                    color: Colors.white,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  strategyLabel,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w800,
                    color: palette.title,
                  ),
                ),
              ),
              Text(
                '权重$weightText',
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w800,
                  color: palette.title,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            '特码命中率：$accuracyText',
            style: TextStyle(fontSize: 12, color: Colors.grey.shade800),
          ),
          const SizedBox(height: 4),
          Text(
            multiplierText,
            style: TextStyle(fontSize: 12, color: Colors.grey.shade800),
          ),
        ],
      ),
    );
  }

  Widget _buildMlInsightCard() {
    if (_resultStrategy != 'ml') {
      return const SizedBox.shrink();
    }
    final meta = _result?['model_meta'];
    if (meta is! Map) {
      return const SizedBox.shrink();
    }
    final metaMap = Map<String, dynamic>.from(meta as Map);

    String fmt(dynamic value, {String suffix = ''}) {
      if (value == null) return '-';
      final text = value.toString();
      if (text.isEmpty) return '-';
      return '$text$suffix';
    }

    final runtimeProfile =
        _mlRuntimeProfileLabel(metaMap['runtime_profile']?.toString() ?? '');
    final featureProfile =
        _mlFeatureProfileLabel(metaMap['feature_profile']?.toString() ?? '');
    final promotionStrength = _mlPromotionStrengthLabel(
      metaMap['promotion_strength']?.toString() ?? '',
    );
    final primaryRuntime = _mlRuntimeProfileLabel(
      metaMap['primary_runtime_profile']?.toString() ?? '',
    );
    final primaryFeature = _mlFeatureProfileLabel(
      metaMap['primary_feature_profile']?.toString() ?? '',
    );
    final displayCopy = Map<String, dynamic>.from(
      (metaMap['display_copy'] as Map?) ?? const {},
    );
    final weightDiagnostics = Map<String, dynamic>.from(
      (metaMap['ensemble_weight_diagnostics'] as Map?) ?? const {},
    );
    final weightKeys = weightDiagnostics.keys.toList()
      ..sort((a, b) {
        final aMap = Map<String, dynamic>.from(
          (weightDiagnostics[a] as Map?) ?? const {},
        );
        final bMap = Map<String, dynamic>.from(
          (weightDiagnostics[b] as Map?) ?? const {},
        );
        return ((bMap['weighted_score'] as num?)?.toDouble() ?? 0.0)
            .compareTo(((aMap['weighted_score'] as num?)?.toDouble() ?? 0.0));
      });

    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(top: 16),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0x14B91C1C),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0x33B91C1C)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            '机器学习诊断',
            style: TextStyle(fontSize: 15, fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _buildInsightMetric('单号参考', fmt(metaMap['top1_hit_rate'], suffix: '%')),
              _buildInsightMetric('六码参考', fmt(metaMap['top6_hit_rate'], suffix: '%')),
              _buildInsightMetric('本期把握度', fmt(metaMap['special_probability'], suffix: '%')),
              _buildInsightMetric('评估样本', '${fmt(metaMap['evaluation_draws'] ?? metaMap['draw_samples'])}期'),
              _buildInsightMetric('参数档位', runtimeProfile),
              _buildInsightMetric('特征档位', featureProfile),
              _buildInsightMetric('固化状态', promotionStrength),
              _buildInsightMetric('综合评分', fmt(metaMap['runtime_score'])),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            (displayCopy['primary_config']?.toString().isNotEmpty ?? false)
                ? displayCopy['primary_config'].toString()
                : '当前主配置：$primaryRuntime · $primaryFeature',
            style: TextStyle(
              fontSize: 12,
              color: Colors.grey.shade800,
              height: 1.5,
            ),
          ),
          if ((displayCopy['preferred_features']?.toString().isNotEmpty ?? false)) ...[
            const SizedBox(height: 8),
            Text(
              displayCopy['preferred_features'].toString(),
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey.shade800,
                height: 1.5,
              ),
            ),
          ],
          if ((displayCopy['preferred_runtimes']?.toString().isNotEmpty ?? false)) ...[
            const SizedBox(height: 8),
            Text(
              displayCopy['preferred_runtimes'].toString(),
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey.shade800,
                height: 1.5,
              ),
            ),
          ],
          if ((displayCopy['color_preference']?.toString().isNotEmpty ?? false)) ...[
            const SizedBox(height: 8),
            Text(
              displayCopy['color_preference'].toString(),
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey.shade800,
                height: 1.5,
              ),
            ),
          ],
          if ((displayCopy['parity_preference']?.toString().isNotEmpty ?? false)) ...[
            const SizedBox(height: 8),
            Text(
              displayCopy['parity_preference'].toString(),
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey.shade800,
                height: 1.5,
              ),
            ),
          ],
          if ((displayCopy['six_reference']?.toString().isNotEmpty ?? false)) ...[
            const SizedBox(height: 8),
            Text(
              displayCopy['six_reference'].toString(),
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey.shade800,
                height: 1.5,
              ),
            ),
          ],
          if ((displayCopy['selected_strategies']?.toString().isNotEmpty ?? false)) ...[
            const SizedBox(height: 8),
            Text(
              displayCopy['selected_strategies'].toString(),
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey.shade800,
                height: 1.5,
              ),
            ),
          ],
          if ((displayCopy['weight_summary']?.toString().isNotEmpty ?? false)) ...[
            const SizedBox(height: 8),
            Text(
              displayCopy['weight_summary'].toString(),
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey.shade800,
                height: 1.5,
              ),
            ),
          ],
          if ((displayCopy['special_votes']?.toString().isNotEmpty ?? false)) ...[
            const SizedBox(height: 8),
            Text(
              displayCopy['special_votes'].toString(),
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey.shade800,
                height: 1.5,
              ),
            ),
          ],
          if (weightKeys.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(
              '近期权重分配依据',
              style: TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w700,
                color: Colors.grey.shade900,
              ),
            ),
            if ((displayCopy['weight_reason_summary']?.toString().isNotEmpty ?? false)) ...[
              const SizedBox(height: 4),
              Text(
                displayCopy['weight_reason_summary'].toString(),
                style: TextStyle(
                  fontSize: 11.5,
                  color: Colors.grey.shade700,
                  height: 1.45,
                ),
              ),
            ],
            ...weightKeys.asMap().entries.map((entry) {
              final rank = entry.key + 1;
              final copyItems =
                  (displayCopy['weight_reason_items'] as List?) ?? const [];
              final copyItem = entry.key < copyItems.length
                  ? Map<String, dynamic>.from(
                      (copyItems[entry.key] as Map?) ?? const {},
                    )
                  : const <String, dynamic>{};
              return _buildMlWeightReasonCard(
                rank,
                copyItem['strategy_label']?.toString() ?? '',
                copyItem['weight_text']?.toString().replaceFirst('权重', '') ?? '',
                copyItem['accuracy_text']?.toString() ?? '',
                copyItem['multiplier_text']?.toString() ?? '',
              );
            }),
          ],
        ],
      ),
    );
  }

  Widget _buildAiMarkdownCard() {
    final markdownText = _aiMarkdownText;
    if (_result == null && markdownText.isEmpty) {
      return const SizedBox.shrink();
    }
    if (markdownText.isEmpty) {
      return Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: _buildPredictionNumbers(),
        ),
      );
    }

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _buildPredictionNumbers(),
            _buildMlInsightCard(),
            const Divider(),
            Text(
              _analysisTitle(),
              style: const TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w800,
              ),
            ),
            const SizedBox(height: 10),
            MarkdownBody(
              data: markdownText,
              selectable: true,
              styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context))
                  .copyWith(
                p: const TextStyle(fontSize: 14, height: 1.6),
                h1: const TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.w700,
                ),
                h2: const TextStyle(
                  fontSize: 20,
                  fontWeight: FontWeight.w700,
                ),
                h3: const TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                ),
                blockquote: TextStyle(
                  color: Colors.grey.shade700,
                  fontStyle: FontStyle.italic,
                ),
                code: TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 13,
                  backgroundColor: Colors.grey.shade200,
                ),
                codeblockDecoration: BoxDecoration(
                  color: const Color(0xFFF5F7FA),
                  borderRadius: BorderRadius.circular(12),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _loadPredictionRecords() async {
    setState(() => _loadingRecords = true);
    try {
      final res = await ApiClient.instance.predictions(
        page: 1,
        pageSize: 24,
        region: _region,
        includeZodiacs: true,
        includeSummaries: false,
        includeDetails: false,
        includeTotal: false,
        year: _currentYear,
      );
      final items = (res['items'] as List<dynamic>? ?? [])
          .map((value) => PredictionItem.fromJson(value as Map<String, dynamic>))
          .toList();
      if (!mounted) return;
      setState(() {
        _predictionRecords = items;
        _showAllPredictionPeriods = false;
        for (final item in items) {
          if (item.normalZodiacs.isNotEmpty) {
            _recordNormalZodiacs[item.id] = item.normalZodiacs;
          }
          if (item.specialZodiac.isNotEmpty) {
            _recordSpecialZodiacs[item.id] = item.specialZodiac;
          }
        }
      });
      _loadPredictionSummaries();
      _loadMissingRecordZodiacs(items);
    } catch (_) {
      if (!mounted) return;
      _showMessage('获取预测记录失败');
    } finally {
      if (mounted) {
        setState(() => _loadingRecords = false);
      }
    }
  }

  Future<void> _loadPredictionSummaries() async {
    try {
      final res = await ApiClient.instance.predictionSummaries(region: _region);
      final summaries = (res['region_summaries'] as List<dynamic>? ?? [])
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .where((e) => (e['region']?.toString() ?? '') == _region)
          .toList();
      if (!mounted) return;
      setState(() => _regionSummaries = summaries);
    } catch (_) {}
  }

  Future<void> _loadMissingRecordZodiacs(List<PredictionItem> items) async {
    final missing = items.where((item) {
      final normal = _recordNormalZodiacs[item.id];
      final hasNormal = normal != null && normal.length >= item.normalNumbers.length;
      final hasSpecial = (_recordSpecialZodiacs[item.id] ?? item.specialZodiac).isNotEmpty;
      return !hasNormal || !hasSpecial;
    }).toList();
    if (missing.isEmpty) return;
    await Future.wait(missing.map(_loadRecordZodiacs));
  }

  Future<void> _loadRecordZodiacs(PredictionItem item) async {
    final normal = _recordNormalZodiacs[item.id];
    final hasNormal = normal != null && normal.length >= item.normalNumbers.length;
    final hasSpecial = (_recordSpecialZodiacs[item.id] ?? item.specialZodiac).isNotEmpty;
    if (hasNormal && hasSpecial) return;
    final numbers = [...item.normalNumbers, item.specialNumber]
        .where((value) => value.isNotEmpty)
        .toList();
    if (numbers.isEmpty) return;
    try {
      final res = await ApiClient.instance.getZodiacs(
        numbers: numbers,
        region: item.region,
        year: _currentYear,
      );
      final normal = (res['normal_zodiacs'] as List<dynamic>? ?? [])
          .map((value) => value.toString())
          .toList();
      final special = res['special_zodiac']?.toString() ?? item.specialZodiac;
      if (!mounted) return;
      setState(() {
        _recordNormalZodiacs[item.id] = normal;
        _recordSpecialZodiacs[item.id] = special;
      });
    } catch (_) {}
  }

  String _resultLabel(PredictionItem item) {
    switch (item.result) {
      case 'special_hit':
        return '中特码';
      case 'wrong':
        return '未命中';
      default:
        return '待开奖';
    }
  }

  Color _resultColor(String value) {
    switch (value) {
      case 'special_hit':
        return const Color(0xFF2563EB);
      case 'wrong':
        return Colors.redAccent;
      default:
        return Colors.orange;
    }
  }

  Color _strategyColor(String value) {
    switch (value) {
      case 'hot':
        return const Color(0xFFE53935);
      case 'cold':
        return const Color(0xFF1E88E5);
      case 'trend':
        return const Color(0xFFB91C1C);
      case 'hybrid':
        return const Color(0xFF6A1B9A);
      case 'balanced':
        return const Color(0xFFFB8C00);
      case 'markov':
        return const Color(0xFF2563EB);
      case 'ml':
        return const Color(0xFFB45309);
      case 'ai':
        return const Color(0xFF5E35B1);
      default:
        return Colors.black87;
    }
  }

  Widget _buildPredictionRecordItem(
    PredictionItem item, {
    bool inlineSpecialOnly = false,
    double? forcedWidth,
  }) {
    final showReferenceNumbers = _showNormalNumbers;
    final normalZodiacs = _recordNormalZodiacs[item.id] ??
        List.filled(item.normalNumbers.length, '');
    final specialZodiac = _recordSpecialZodiacs[item.id] ?? item.specialZodiac;
    final strategyLabel = _strategyLabels[item.strategy] ?? item.strategy;
    final accentColor = _strategyColor(item.strategy);
    final statusColor = _resultColor(item.result);
    final itemRegionLabel = item.region == 'macau' ? '澳门' : '香港';
    return Container(
      width: forcedWidth ?? (inlineSpecialOnly ? 164 : null),
      margin: EdgeInsets.only(
        bottom: inlineSpecialOnly ? 0 : 12,
        right: 0,
      ),
      padding: EdgeInsets.all(inlineSpecialOnly ? 7 : 12),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            Colors.white,
            Color(0xFFF8FBFF),
          ],
        ),
        borderRadius: BorderRadius.circular(inlineSpecialOnly ? 16 : 20),
        border: Border.all(color: accentColor.withOpacity(0.14)),
        boxShadow: [
          BoxShadow(
            color: Color(0x14000000),
            blurRadius: inlineSpecialOnly ? 10 : 14,
            offset: Offset(0, inlineSpecialOnly ? 4 : 6),
          ),
          BoxShadow(
            color: accentColor.withOpacity(0.06),
            blurRadius: inlineSpecialOnly ? 12 : 18,
            offset: Offset(0, inlineSpecialOnly ? 5 : 8),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 8,
                  vertical: 4,
                ),
                decoration: BoxDecoration(
                  color: accentColor.withOpacity(0.10),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Text(
                  strategyLabel,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: accentColor,
                    fontSize: inlineSpecialOnly ? 12 : 13,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              const SizedBox(height: 8),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: statusColor.withOpacity(0.10),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Text(
                  _resultLabel(item),
                  style: TextStyle(
                    color: statusColor,
                    fontSize: inlineSpecialOnly ? 11 : 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              if (!inlineSpecialOnly) ...[
                const SizedBox(height: 6),
                Text(
                  '$itemRegionLabel预测记录',
                  style: TextStyle(
                    color: Colors.grey.shade600,
                    fontSize: 10,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ],
            ],
          ),
          SizedBox(height: inlineSpecialOnly ? 7 : (showReferenceNumbers ? 12 : 10)),
          if (showReferenceNumbers) ...[
            Text(
              '平码参考',
              style: TextStyle(
                fontWeight: FontWeight.w700,
                color: Colors.grey.shade800,
                fontSize: 12,
              ),
            ),
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: const Color(0xFFF5F8FC),
                borderRadius: BorderRadius.circular(16),
              ),
              child: _buildNumberGrid(
                numbers: item.normalNumbers,
                zodiacs: normalZodiacs,
                ballSize: 34,
                numberFontSize: 13,
                zodiacFontSize: 11,
                gap: 3,
                childAspectRatio: 0.85,
              ),
            ),
            const SizedBox(height: 12),
          ],
          Container(
            width: double.infinity,
            padding: EdgeInsets.symmetric(
              horizontal: inlineSpecialOnly ? 5 : 10,
              vertical: inlineSpecialOnly ? 7 : 12,
            ),
            decoration: BoxDecoration(
              color: accentColor.withOpacity(0.06),
              borderRadius: BorderRadius.circular(inlineSpecialOnly ? 14 : 18),
            ),
            child: inlineSpecialOnly
                ? Center(
                    child: item.specialNumber.isNotEmpty
                        ? _NumberZodiacTile(
                            number: item.specialNumber,
                            zodiac: specialZodiac,
                            color: ballColor(item.specialNumber),
                            outlined: true,
                            highlight: true,
                            ballSize: 30,
                            numberFontSize: 12,
                            zodiacFontSize: 10,
                            gap: 2,
                          )
                        : const SizedBox.shrink(),
                  )
                : Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              '本期主推特码',
                              style: TextStyle(
                                color: Colors.grey.shade700,
                                fontSize: 10,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(height: 3),
                            Text(
                              specialZodiac.isNotEmpty
                                  ? '生肖 $specialZodiac'
                                  : '重点参考号码',
                              style: TextStyle(
                                color: accentColor,
                                fontSize: 13,
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 10),
                      if (item.specialNumber.isNotEmpty)
                        _NumberZodiacTile(
                          number: item.specialNumber,
                          zodiac: specialZodiac,
                          color: ballColor(item.specialNumber),
                          outlined: true,
                          highlight: true,
                          ballSize: 42,
                          numberFontSize: 15,
                          zodiacFontSize: 11,
                          gap: 3,
                        ),
                    ],
                  ),
          ),
        ],
      ),
    );
  }

  Widget _buildPredictionPeriodHeader({
    required String period,
    required String actualSpecialNumber,
    required String actualSpecialZodiac,
    required bool hit,
  }) {
    final hasResult = actualSpecialNumber.isNotEmpty;
    final accentColor = hit ? const Color(0xFFE11D48) : const Color(0xFF334155);

    return Wrap(
      crossAxisAlignment: WrapCrossAlignment.center,
      spacing: 8,
      runSpacing: 6,
      children: [
        Text(
          '期号：$period',
          style: const TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w800,
            color: Color(0xFF1F2937),
          ),
        ),
        if (hasResult) ...[
          const Text(
            '开奖结果：',
            style: TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w700,
              color: Color(0xFF1F2937),
            ),
          ),
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                decoration: BoxDecoration(
                  gradient: hit
                      ? const LinearGradient(
                          colors: [Color(0xFFFF3B30), Color(0xFFFFB020)],
                        )
                      : null,
                  color: hit ? null : const Color(0xFFF1F5F9),
                  borderRadius: BorderRadius.circular(999),
                  border: Border.all(
                    color: hit
                        ? const Color(0xFFFFC2D1)
                        : const Color(0xFFE2E8F0),
                    width: hit ? 1.4 : 1,
                  ),
                  boxShadow: hit
                      ? const [
                          BoxShadow(
                            color: Color(0x44FF4D4F),
                            blurRadius: 10,
                            spreadRadius: 1,
                          ),
                        ]
                      : [],
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (hit) ...[
                      const Icon(
                        Icons.auto_awesome,
                        size: 14,
                        color: Colors.white,
                      ),
                      const SizedBox(width: 4),
                    ],
                    Text(
                      actualSpecialNumber,
                      style: TextStyle(
                        color: hit ? Colors.white : accentColor,
                        fontSize: 14,
                        fontWeight: FontWeight.w900,
                      ),
                    ),
                  ],
                ),
              ),
              if (actualSpecialZodiac.isNotEmpty) ...[
                const SizedBox(width: 8),
                Text(
                  '生肖：$actualSpecialZodiac',
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w900,
                    color: hit
                        ? const Color(0xFFE11D48)
                        : const Color(0xFF1F2937),
                  ),
                ),
              ],
            ],
          ),
        ],
      ],
    );
  }

  Widget _buildSummaryCards() {
    final summaries = _regionSummaries
        .where((card) => (card['region']?.toString() ?? '') == _region)
        .toList();
    if (summaries.isEmpty) return const SizedBox.shrink();

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: summaries.asMap().entries.map((entry) {
        final index = entry.key;
        final card = entry.value;
        final label = card['region_label']?.toString() ?? '';
        final missStreak = (card['miss_streak'] as num?)?.toInt() ?? 0;
        final maxMissStreak = (card['max_miss_streak'] as num?)?.toInt() ?? 0;
        final maxHitStreak = (card['max_hit_streak'] as num?)?.toInt() ?? 0;
        final hitPeriods = (card['hit_periods'] as num?)?.toInt() ?? 0;
        final accuracy = (card['accuracy'] as num?)?.toDouble() ?? 0.0;
        final accuracyText = '${accuracy.toStringAsFixed(1)}%';

        return Expanded(
          child: Container(
            margin: EdgeInsets.only(
              bottom: 12,
              right: index < summaries.length - 1 ? 8 : 0,
              left: index > 0 ? 8 : 0,
            ),
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                colors: [Color(0xFFFFFBFB), Color(0xFFFFFFFF)],
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
              ),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: const Color(0xFFFFE1E6)),
              boxShadow: const [
                BoxShadow(
                  color: Color(0x14000000),
                  blurRadius: 10,
                  offset: Offset(0, 4),
                ),
              ],
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 10, 12, 8),
                  child: Row(
                    children: [
                      Container(
                        width: 26,
                        height: 26,
                        decoration: BoxDecoration(
                          color: const Color(0xFFFFEEF2),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: const Icon(
                          Icons.trending_up,
                          color: Color(0xFFB91C1C),
                          size: 17,
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '$label走势',
                          style: const TextStyle(
                            fontWeight: FontWeight.w900,
                            fontSize: 15,
                            color: Color(0xFF111827),
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 5,
                        ),
                        decoration: BoxDecoration(
                          color: const Color(0xFFB91C1C),
                          borderRadius: BorderRadius.circular(999),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const Text(
                              '特码命中率',
                              style: TextStyle(
                                color: Colors.white,
                                fontSize: 11,
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                            const SizedBox(width: 5),
                            Text(
                              accuracyText,
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 12,
                                fontWeight: FontWeight.w900,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(8, 0, 8, 10),
                  child: LayoutBuilder(
                    builder: (context, constraints) {
                      final columns = constraints.maxWidth >= 340 ? 4 : 2;
                      return GridView.count(
                        crossAxisCount: columns,
                        mainAxisSpacing: 6,
                        crossAxisSpacing: 6,
                        childAspectRatio: columns == 4 ? 1.6 : 2.5,
                        shrinkWrap: true,
                        physics: const NeverScrollableScrollPhysics(),
                        children: [
                          _buildSummaryStatItem(
                            '当前连错',
                            missStreak.toString(),
                            missStreak > 0
                                ? const Color(0xFFE11D48)
                                : const Color(0xFF2563EB),
                            const Color(0xFFFFF1F2),
                          ),
                          _buildSummaryStatItem(
                            '最高连错',
                            maxMissStreak.toString(),
                            const Color(0xFFE11D48),
                            const Color(0xFFFFF1F2),
                          ),
                          _buildSummaryStatItem(
                            '最高连中',
                            maxHitStreak.toString(),
                            const Color(0xFF2563EB),
                            const Color(0xFFEFF6FF),
                          ),
                          _buildSummaryStatItem(
                            '累计中特',
                            hitPeriods.toString(),
                            const Color(0xFF2563EB),
                            const Color(0xFFEFF6FF),
                          ),
                        ],
                      );
                    },
                  ),
                ),
              ],
            ),
          ),
        );
      }).toList(),
    );
  }

  Widget _buildSummaryStatItem(
    String label,
    String value,
    Color valueColor,
    Color backgroundColor,
  ) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 5),
      decoration: BoxDecoration(
        color: backgroundColor,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Colors.white),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            label,
            style: const TextStyle(
              fontSize: 10,
              color: Color(0xFF6B7280),
              fontWeight: FontWeight.w600,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 4),
          Text(
            value,
            style: TextStyle(
              fontSize: 15,
              fontWeight: FontWeight.w900,
              color: valueColor,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }

  Widget _buildPredictionRecordsSection() {
    final regionLabel = _region == 'macau' ? '澳门' : '香港';
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    '$regionLabel预测记录',
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                IconButton(
                  onPressed: _loadingRecords ? null : _loadPredictionRecords,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            if (_loadingRecords)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_predictionRecords.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Text('暂无预测记录'),
              )
            else
              Builder(
                builder: (context) {
                  final grouped = <String, List<PredictionItem>>{};
                  final orderedPeriods = <String>[];
                  final displayPeriods = <String, String>{};
                  for (final item in _predictionRecords) {
                    final groupKey = '${item.region}:${item.period}';
                    if (!grouped.containsKey(groupKey)) {
                      grouped[groupKey] = [];
                      orderedPeriods.add(groupKey);
                      displayPeriods[groupKey] = item.period;
                    }
                    grouped[groupKey]!.add(item);
                  }
                  for (final entry in grouped.entries) {
                    entry.value.sort((a, b) {
                      final aNum = int.tryParse(a.specialNumber) ?? 999;
                      final bNum = int.tryParse(b.specialNumber) ?? 999;
                      return aNum.compareTo(bNum);
                    });
                  }

                  final visiblePeriods = _showAllPredictionPeriods
                      ? orderedPeriods
                      : orderedPeriods.take(3).toList();

                  return Column(
                    children: [
                      ...visiblePeriods.map((periodKey) {
                        final items = grouped[periodKey] ?? [];
                        final period = displayPeriods[periodKey] ?? periodKey;
                        final firstItem =
                            items.isNotEmpty ? items.first : null;
                        final actualSpecialNumber =
                            firstItem?.actualSpecialNumber ?? '';
                        final actualSpecialZodiac =
                            firstItem?.actualSpecialZodiac ?? '';
                        final periodHit =
                            items.any((item) => item.result == 'special_hit');
                        return Container(
                          margin: const EdgeInsets.only(bottom: 12),
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            gradient: LinearGradient(
                              begin: Alignment.topLeft,
                              end: Alignment.bottomRight,
                              colors: periodHit
                                  ? const [
                                      Color(0xFFFFFBF2),
                                      Color(0xFFFFF1F2),
                                    ]
                                  : const [
                                      Color(0xFFF9FCFF),
                                      Color(0xFFF5FAF8),
                                    ],
                            ),
                            borderRadius: BorderRadius.circular(18),
                            border: Border.all(
                              color: periodHit
                                  ? const Color(0xFFFFC2D1)
                                  : const Color(0xFFE2E8F0),
                            ),
                            boxShadow: [
                              BoxShadow(
                                color: periodHit
                                    ? const Color(0x24E11D48)
                                    : const Color(0x12000000),
                                blurRadius: periodHit ? 14 : 10,
                                offset: Offset(0, 5),
                              ),
                            ],
                            ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              _buildPredictionPeriodHeader(
                                period: period,
                                actualSpecialNumber: actualSpecialNumber,
                                actualSpecialZodiac: actualSpecialZodiac,
                                hit: periodHit,
                              ),
                              const SizedBox(height: 8),
                              if (_showNormalNumbers)
                                Column(
                                  children: items
                                      .map(_buildPredictionRecordItem)
                                      .toList(),
                                )
                              else
                                LayoutBuilder(
                                  builder: (context, constraints) {
                                    const spacing = 8.0;
                                    final cardWidth =
                                        (constraints.maxWidth - spacing * 2) / 3;
                                    return Align(
                                      alignment: Alignment.centerLeft,
                                      child: Wrap(
                                        spacing: spacing,
                                        runSpacing: spacing,
                                        children: items
                                            .map(
                                              (item) => _buildPredictionRecordItem(
                                                item,
                                                inlineSpecialOnly: true,
                                                forcedWidth: cardWidth,
                                              ),
                                            )
                                            .toList(),
                                      ),
                                    );
                                  },
                                ),
                            ],
                          ),
                        );
                      }).toList(),
                      if (!_showAllPredictionPeriods &&
                          orderedPeriods.length > 3)
                        TextButton(
                          onPressed: () {
                            setState(() {
                              _showAllPredictionPeriods = true;
                            });
                          },
                          child: const Text('显示更多'),
                        ),
                      if (_showAllPredictionPeriods &&
                          orderedPeriods.length > 3)
                        TextButton(
                          onPressed: () {
                            setState(() {
                              _showAllPredictionPeriods = false;
                            });
                          },
                          child: const Text('收起'),
                        ),
                    ],
                  );
                },
              ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final activationValid = widget.appState.activationValid;

    return Scaffold(
      appBar: AppBar(title: const Text('号码预测')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            Card(
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(16),
              ),
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  children: [
                    Row(
                      children: [
                        _buildPredictionRegionButton('macau', '澳门'),
                        const SizedBox(width: 8),
                        _buildPredictionRegionButton('hk', '香港'),
                      ],
                    ),
                    const SizedBox(height: 12),
                    Row(
                      children: [
                        Container(
                          width: 4,
                          height: 18,
                          decoration: BoxDecoration(
                            color: const Color(0xFFB91C1C),
                            borderRadius: BorderRadius.circular(99),
                          ),
                        ),
                        const SizedBox(width: 8),
                        const Text(
                          '预测策略',
                          style: TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.w800,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    LayoutBuilder(
                      builder: (context, constraints) {
                        const spacing = 8.0;
                        final itemWidth =
                            (constraints.maxWidth - spacing * 2) / 3;
                        return Wrap(
                          spacing: spacing,
                          runSpacing: spacing,
                          children: _strategyLabels.entries.map((entry) {
                            return SizedBox(
                              width: itemWidth,
                              child: _buildStrategyChip(
                                entry.key,
                                entry.value,
                              ),
                            );
                          }).toList(),
                        );
                      },
                    ),
                    const SizedBox(height: 10),
                    AnimatedSwitcher(
                      duration: const Duration(milliseconds: 180),
                      child: _loading
                          ? const SizedBox(
                              key: ValueKey('loading'),
                              height: 24,
                              child: Center(
                                child: SizedBox(
                                  height: 20,
                                  width: 20,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                ),
                              ),
                            )
                          : const SizedBox.shrink(),
                    ),
                    if (!activationValid)
                      const Padding(
                        padding: EdgeInsets.only(top: 8),
                        child: Text(
                          '账号未激活或已过期，请先激活后使用号码预测功能。',
                          style: TextStyle(color: Colors.orange),
                        ),
                      ),
                    if (!activationValid)
                      Align(
                        alignment: Alignment.centerRight,
                        child: TextButton(
                          onPressed: _promptActivation,
                          child: const Text('去激活'),
                        ),
                      ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            Expanded(
              child: ListView(
                children: [
                  if (_result != null || _aiMarkdownText.isNotEmpty) ...[
                    _buildAiMarkdownCard(),
                    const SizedBox(height: 16),
                  ],
                  _buildSummaryCards(),
                  _buildPredictionRecordsSection(),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  String get _currentYear => DateTime.now().year.toString();
  // 预测请求继续传公历年份，后端会按当前农历生肖年切换预测取数。
  String get _predictionYear => _currentYear;

  List<String> _uniqueNumbers(List<String> values) {
    final seen = <String>{};
    final result = <String>[];
    for (final value in values) {
      final v = value.trim();
      if (v.isEmpty || seen.contains(v)) continue;
      seen.add(v);
      result.add(v);
    }
    return result;
  }

  List<String> _removeSpecialFromNormal(
      List<String> normal, String special) {
    if (special.isEmpty) return normal;
    return normal.where((value) => value != special).toList();
  }
}

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key, required this.appState});

  final AppState appState;

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  AccuracyStats? _overall;
  bool _loading = false;
  String _appVersion = '';
  String _versionStatus = '';
  Map<String, dynamic>? _betSummary;
  bool _loadingBetSummary = false;
  bool _savingDisplaySettings = false;

  @override
  void initState() {
    super.initState();
    _loadAccuracy();
    _loadVersion();
    _loadVersionStatus();
    _loadBetSummary();
  }

  Future<void> _loadAccuracy() async {
    setState(() => _loading = true);
    try {
      final res = await ApiClient.instance.accuracy();
      setState(() {
        _overall = AccuracyStats.fromJson(res['overall'] as Map<String, dynamic>);
      });
    } catch (_) {
      _showMessage('获取准确率失败');
    } finally {
      if (mounted) {
        setState(() => _loading = false);
      }
    }
  }

  Future<void> _loadVersion() async {
    try {
      final info = await PackageInfo.fromPlatform();
      if (!mounted) return;
      setState(() => _appVersion = UpdateService.formatVersion(info.version));
    } catch (_) {}
  }

  Future<void> _loadVersionStatus() async {
    await UpdateService.checkForUpdate(
      context,
      onCurrentVersionStatus: (status) {
        if (!mounted) return;
        setState(() => _versionStatus = status);
      },
      showUpdatePrompt: false,
    );
  }

  Future<void> _loadBetSummary() async {
    setState(() => _loadingBetSummary = true);
    try {
      final res = await ApiClient.instance.manualBetSummary();
      if (!mounted) return;
      setState(() {
        _betSummary = res['summary'] as Map<String, dynamic>?;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _betSummary = null);
    } finally {
      if (mounted) {
        setState(() => _loadingBetSummary = false);
      }
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  String _formatYuan(num? value) {
    if (value == null) return '-';
    return '${value.toStringAsFixed(0)}元';
  }

  Widget _buildProfileSectionHeader({
    required IconData icon,
    required String title,
    Color color = const Color(0xFFB91C1C),
    Widget? trailing,
  }) {
    return Row(
      children: [
        Container(
          width: 34,
          height: 34,
          decoration: BoxDecoration(
            color: color.withOpacity(0.12),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Icon(icon, color: color, size: 20),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Text(
            title,
            style: const TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w800,
            ),
          ),
        ),
        if (trailing != null) trailing,
      ],
    );
  }

  Widget _buildMetricTile({
    required String label,
    required String value,
    required Color color,
    IconData? icon,
  }) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: color.withOpacity(0.08),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: color.withOpacity(0.16)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (icon != null) ...[
                Icon(icon, size: 15, color: color),
                const SizedBox(width: 5),
              ],
              Expanded(
                child: Text(
                  label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: Colors.grey.shade700,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              color: color,
              fontSize: 20,
              fontWeight: FontWeight.w900,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildMetricGrid(List<Widget> children) {
    return LayoutBuilder(
      builder: (context, constraints) {
        const spacing = 10.0;
        final itemWidth = (constraints.maxWidth - spacing) / 2;
        return Wrap(
          spacing: spacing,
          runSpacing: spacing,
          children: children
              .map((child) => SizedBox(width: itemWidth, child: child))
              .toList(),
        );
      },
    );
  }

  Future<void> _showChangePasswordDialog() async {
    final currentController = TextEditingController();
    final newController = TextEditingController();
    final confirmController = TextEditingController();
    bool saving = false;

    try {
      await showDialog<void>(
        context: context,
        builder: (dialogContext) {
          return StatefulBuilder(
            builder: (context, setDialogState) {
              return AlertDialog(
                title: const Text('修改密码'),
                content: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TextField(
                      controller: currentController,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: '当前密码'),
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: newController,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: '新密码'),
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: confirmController,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: '确认新密码'),
                    ),
                  ],
                ),
                actions: [
                  TextButton(
                    onPressed: saving
                        ? null
                        : () => Navigator.of(dialogContext).pop(),
                    child: const Text('取消'),
                  ),
                  ElevatedButton(
                    onPressed: saving
                        ? null
                        : () async {
                            final current = currentController.text.trim();
                            final next = newController.text.trim();
                            final confirm = confirmController.text.trim();

                            if (current.isEmpty || next.isEmpty || confirm.isEmpty) {
                              _showMessage('请填写完整');
                              return;
                            }
                            if (next.length < 6) {
                              _showMessage('新密码至少6位');
                              return;
                            }
                            if (next != confirm) {
                              _showMessage('两次输入的新密码不一致');
                              return;
                            }

                            setDialogState(() => saving = true);
                            final error = await widget.appState.changePassword(
                              currentPassword: current,
                              newPassword: next,
                              confirmPassword: confirm,
                            );
                            if (!mounted) return;
                            setDialogState(() => saving = false);

                            if (error == null) {
                              Navigator.of(dialogContext).pop();
                              _showMessage('密码修改成功');
                            } else {
                              _showMessage(error);
                            }
                          },
                    child: saving
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Text('保存'),
                  ),
                ],
              );
            },
          );
        },
      );
    } finally {
      currentController.dispose();
      newController.dispose();
      confirmController.dispose();
    }
  }

  Future<void> _toggleShowNormalNumbers(bool value) async {
    setState(() => _savingDisplaySettings = true);
    final error = await widget.appState.updateShowNormalNumbers(value);
    if (!mounted) return;
    setState(() => _savingDisplaySettings = false);
    if (error != null) {
      _showMessage(error);
      return;
    }
    _showMessage(value ? '已开启平码显示' : '已关闭平码显示');
  }

  @override
  Widget build(BuildContext context) {
    final user = widget.appState.user;
    if (user == null) {
      return const SizedBox.shrink();
    }
    final activationValid = widget.appState.activationValid;
    final activationExpired =
        user.isActive && user.activationExpiresAt != null && !activationValid;

    return Scaffold(
      appBar: AppBar(title: const Text('个人中心')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
            ),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.center,
                    children: [
                      Expanded(
                        child: Text(
                          user.username,
                          style: const TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                      const SizedBox(width: 12),
                      OutlinedButton.icon(
                        onPressed: _showChangePasswordDialog,
                        icon: const Icon(Icons.lock_outline, size: 18),
                        label: const Text('修改密码'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Text(user.email),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      Chip(
                        label: Text(
                          activationValid
                              ? '已激活'
                              : activationExpired
                                  ? '已过期'
                                  : '未激活',
                        ),
                        backgroundColor:
                            activationValid
                                ? Colors.amber.shade100
                                : activationExpired
                                    ? Colors.red.shade100
                                    : Colors.orange.shade100,
                      ),
                      if (user.activationExpiresAt != null)
                        Text('到期：${user.activationExpiresAt}'),
                    ],
                  ),
                  if (!activationValid)
                    Padding(
                      padding: const EdgeInsets.only(top: 12),
                      child: ElevatedButton(
                        onPressed: () =>
                            showActivationDialog(context, widget.appState),
                        child: const Text('输入激活码'),
                      ),
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          Card(
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
            ),
            child: SwitchListTile(
              value: user.showNormalNumbers,
              onChanged: _savingDisplaySettings ? null : _toggleShowNormalNumbers,
              title: const Text('显示平码'),
              subtitle: const Text('默认仅展示特码，开启后在预测结果和预测记录中同时显示平码'),
            ),
          ),
          const SizedBox(height: 16),
          Card(
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
            ),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: _loading
                  ? const Center(child: CircularProgressIndicator())
                  : Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            const Text(
                              '当前版本：',
                              style: TextStyle(fontWeight: FontWeight.bold),
                            ),
                            Text(_appVersion.isEmpty ? '-' : _appVersion),
                            if (_versionStatus.isNotEmpty) ...[
                              const SizedBox(width: 8),
                              Text(
                                _versionStatus,
                                style: TextStyle(
                                  color: Colors.grey.shade600,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                            ],
                          ],
                        ),
                        const SizedBox(height: 12),
                        _buildProfileSectionHeader(
                          icon: Icons.track_changes,
                          title: '特码准确率',
                          color: const Color(0xFF2563EB),
                        ),
                        const SizedBox(height: 12),
                        if (_overall != null) ...[
                          _buildMetricGrid([
                            _buildMetricTile(
                              label: '特码命中率',
                              value: '${_overall!.accuracy}%',
                              color: const Color(0xFF2563EB),
                              icon: Icons.center_focus_strong,
                            ),
                            _buildMetricTile(
                              label: '平码/生肖命中率',
                              value: '${_overall!.normalHitRate}%',
                              color: const Color(0xFFB91C1C),
                              icon: Icons.grid_view,
                            ),
                            _buildMetricTile(
                              label: '总预测次数',
                              value: _overall!.total.toString(),
                              color: const Color(0xFF7C3AED),
                              icon: Icons.analytics_outlined,
                            ),
                            _buildMetricTile(
                              label: '累计命中',
                              value:
                                  '${_overall!.specialHits}/${_overall!.normalHits}',
                              color: const Color(0xFFF97316),
                              icon: Icons.done_all,
                            ),
                          ]),
                        ] else
                          const Text('暂无数据'),
                      ],
                    ),
            ),
          ),
          const SizedBox(height: 16),
          Card(
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
            ),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: _loadingBetSummary
                  ? const Center(child: CircularProgressIndicator())
                  : Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _buildProfileSectionHeader(
                          icon: Icons.account_balance_wallet_outlined,
                          title: '盈亏报表',
                          color: const Color(0xFFB91C1C),
                          trailing: IconButton(
                            onPressed:
                                _loadingBetSummary ? null : _loadBetSummary,
                            icon: const Icon(Icons.refresh),
                          ),
                        ),
                        const SizedBox(height: 12),
                        if (_betSummary != null) ...[
                          _buildMetricGrid([
                            _buildMetricTile(
                              label: '总下注',
                              value: _formatYuan(
                                ((_betSummary!['total_stake'] as num?) ?? 0)
                                    .toDouble(),
                              ),
                              color: const Color(0xFFB91C1C),
                              icon: Icons.payments_outlined,
                            ),
                            _buildMetricTile(
                              label: '总盈亏',
                              value: _formatYuan(
                                ((_betSummary!['total_profit'] as num?) ?? 0)
                                    .toDouble(),
                              ),
                              color:
                                  (((_betSummary!['total_profit'] as num?) ?? 0)
                                              .toDouble() <
                                          0)
                                      ? const Color(0xFFDC2626)
                                      : const Color(0xFF2563EB),
                              icon: Icons.show_chart,
                            ),
                            _buildMetricTile(
                              label: '已结算 / 待结算',
                              value:
                                  '${_betSummary!['settled_count'] ?? 0}/${_betSummary!['pending_count'] ?? 0}',
                              color: const Color(0xFF7C3AED),
                              icon: Icons.fact_check_outlined,
                            ),
                            _buildMetricTile(
                              label: '赢 / 输 / 平',
                              value:
                                  '${_betSummary!['win_count'] ?? 0}/${_betSummary!['lose_count'] ?? 0}/${_betSummary!['draw_count'] ?? 0}',
                              color: const Color(0xFFF97316),
                              icon: Icons.emoji_events_outlined,
                            ),
                          ]),
                        ] else
                          const Text('暂无数据'),
                      ],
                    ),
            ),
          ),
          const SizedBox(height: 12),
          OutlinedButton(
            onPressed: () => widget.appState.logout(),
            child: const Text('退出登录'),
          ),
        ],
      ),
    );
  }
}

class UpdateService {
  static const String _owner = 'e5sub';
  static const String _repo = 'mark-six';
  static const String _apkName = 'app-release.apk';
  static const String _proxy = 'https://gh-proxy.com/';

  static Future<void> checkForUpdate(
    BuildContext context, {
    void Function(String status)? onCurrentVersionStatus,
    bool showUpdatePrompt = true,
  }) async {
    try {
      final info = await PackageInfo.fromPlatform();
      final currentVersion = info.version;
      final latest = await _fetchLatestRelease();
      if (latest == null) return;

      final latestVersion = latest.version;
      if (_compareVersions(latestVersion, currentVersion) <= 0) {
        onCurrentVersionStatus?.call('已是最新版本');
        return;
      }

      onCurrentVersionStatus?.call(
        '发现新版本 ${formatVersion(latestVersion)}',
      );
      if (!showUpdatePrompt) return;

      if (!context.mounted) return;
      final shouldUpdate = await showDialog<bool>(
            context: context,
            builder: (dialogContext) => AlertDialog(
              title: const Text('检测到新版本'),
              content: Text(
                '当前版本 ${formatVersion(currentVersion)}，'
                '最新版本 ${formatVersion(latestVersion)}，是否立即更新？',
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.of(dialogContext).pop(false),
                  child: const Text('稍后'),
                ),
                TextButton(
                  onPressed: () => Navigator.of(dialogContext).pop(true),
                  child: const Text('下载更新'),
                ),
              ],
            ),
          ) ??
          false;

      if (!shouldUpdate || !context.mounted) return;
      await _downloadAndInstall(context, latest.downloadUrl);
    } catch (_) {
      // ignore update errors
    }
  }

  static String formatVersion(String version) {
    final raw = version.split('+').first.trim();
    if (raw.isEmpty) return '-';
    final parts = raw.split('.').where((part) => part.isNotEmpty).toList();
    while (parts.length > 2 && parts.last == '0') {
      parts.removeLast();
    }
    return parts.join('.');
  }

  static Future<_ReleaseInfo?> _fetchLatestRelease() async {
    final url =
        '${_proxy}https://api.github.com/repos/$_owner/$_repo/releases/latest';
    final response = await Dio().get(url);
    if (response.statusCode != 200 || response.data is! Map) {
      return null;
    }
    final data = response.data as Map;
    final tagName = data['tag_name']?.toString() ?? '';
    final version = tagName.startsWith('v') ? tagName.substring(1) : tagName;
    final assets = data['assets'] as List<dynamic>? ?? [];
    Map<String, dynamic>? asset;
    for (final item in assets) {
      if (item is Map && item['name']?.toString() == _apkName) {
        asset = Map<String, dynamic>.from(item as Map);
        break;
      }
    }
    if (asset == null) return null;
    final rawUrl = asset['browser_download_url']?.toString();
    final downloadUrl =
        rawUrl == null || rawUrl.isEmpty ? null : '$_proxy$rawUrl';
    if (downloadUrl == null || downloadUrl.isEmpty) return null;
    return _ReleaseInfo(version: version, downloadUrl: downloadUrl);
  }

  static Future<void> _downloadAndInstall(
      BuildContext context, String url) async {
    final tempDir = await getTemporaryDirectory();
    final filePath = '${tempDir.path}/$_apkName';
    double progress = 0;
    StateSetter? dialogSetState;

    if (!context.mounted) return;
    showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setState) {
          dialogSetState = setState;
          return AlertDialog(
            title: const Text('正在下载更新'),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                LinearProgressIndicator(value: progress),
                const SizedBox(height: 12),
                Text('${(progress * 100).toStringAsFixed(0)}%'),
              ],
            ),
          );
        },
      ),
    );

    await Dio().download(
      url,
      filePath,
      onReceiveProgress: (received, total) {
        if (total <= 0) return;
        progress = received / total;
        dialogSetState?.call(() {});
      },
    );

    if (context.mounted) {
      Navigator.of(context, rootNavigator: true).pop();
    }

    await OpenFilex.open(filePath);
  }

  static int _compareVersions(String a, String b) {
    final aParts = _normalizeVersion(a).map(_safeParseInt).toList();
    final bParts = _normalizeVersion(b).map(_safeParseInt).toList();
    final maxLen = aParts.length > bParts.length ? aParts.length : bParts.length;
    for (var i = 0; i < maxLen; i++) {
      final aVal = i < aParts.length ? aParts[i] : 0;
      final bVal = i < bParts.length ? bParts[i] : 0;
      if (aVal != bVal) {
        return aVal.compareTo(bVal);
      }
    }
    return 0;
  }

  static List<String> _normalizeVersion(String version) {
    final raw = version.split('+').first.trim();
    final parts = raw.split('.');
    if (parts.length <= 2) return parts;
    return parts.take(2).toList();
  }

  static int _safeParseInt(String value) {
    return int.tryParse(value) ?? 0;
  }
}

class _ReleaseInfo {
  _ReleaseInfo({required this.version, required this.downloadUrl});

  final String version;
  final String downloadUrl;
}
