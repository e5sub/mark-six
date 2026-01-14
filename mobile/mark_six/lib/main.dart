
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:open_filex/open_filex.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';

import 'api_client.dart';
import 'models.dart';

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
      title: '彩票数据分析',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF0B6B4F),
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
    await loadMe();
    initialized = true;
    notifyListeners();
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

  Future<String?> login(String usernameOrEmail, String password) async {
    try {
      final res = await ApiClient.instance.login(
        usernameOrEmail: usernameOrEmail,
        password: password,
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
  }) async {
    try {
      final res = await ApiClient.instance.register(
        username: username,
        email: email,
        password: password,
        confirmPassword: confirmPassword,
        inviteCode: inviteCode,
      );
      if (res['success'] == true) {
        return null;
      }
      return res['message']?.toString() ?? '注册失败';
    } catch (e) {
      return '注册失败: $e';
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
}

Future<void> showActivationDialog(
  BuildContext context,
  AppState appState,
) async {
  final controller = TextEditingController();
  String? result;
  try {
    result = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('激活账号'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(labelText: '激活码'),
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
    );
  } finally {
    controller.dispose();
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

  @override
  void dispose() {
    _username.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _handleLogin() async {
    setState(() => _loading = true);
    final error = await widget.appState.login(
      _username.text.trim(),
      _password.text,
    );
    setState(() => _loading = false);
    if (error != null && mounted) {
      _showMessage(error);
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
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            colors: [Color(0xFF0B6B4F), Color(0xFF0F9D58)],
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
                      '彩票数据分析',
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
                    const SizedBox(height: 24),
                    SizedBox(
                      width: double.infinity,
                      child: ElevatedButton(
                        style: ElevatedButton.styleFrom(
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
                    TextButton(
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
    setState(() => _loading = true);
    final error = await widget.appState.register(
      username: _username.text.trim(),
      email: _email.text.trim(),
      password: _password.text,
      confirmPassword: _confirmPassword.text,
      inviteCode: _inviteCode.text.trim(),
    );
    setState(() => _loading = false);

    if (!mounted) return;
    if (error != null) {
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
                : GridView.builder(
                    padding: const EdgeInsets.all(4),
                    physics: const AlwaysScrollableScrollPhysics(),
                    gridDelegate:
                        const SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: 4,
                      mainAxisSpacing: 4,
                      crossAxisSpacing: 4,
                      childAspectRatio: 1.08,
                    ),
                    itemCount: _items.length,
                    itemBuilder: (context, index) {
                      final item = _items[index];
                      final color = ballColor(item.number);
                      return Container(
                        decoration: BoxDecoration(
                          color: Colors.white,
                          borderRadius: BorderRadius.circular(16),
                          boxShadow: [
                            BoxShadow(
                              color: Colors.black.withOpacity(0.06),
                              blurRadius: 10,
                              offset: const Offset(0, 4),
                            ),
                          ],
                        ),
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            _Ball(
                              number: item.number,
                              color: color,
                              size: 34,
                              fontSize: 12,
                            ),
                            const SizedBox(height: 4),
                            Text(
                              item.zodiac,
                              style: TextStyle(
                                fontSize: 12,
                                fontWeight: FontWeight.w600,
                                color: Colors.grey.shade700,
                              ),
                            ),
                          ],
                        ),
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

  String _region = 'hk';
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

  bool get _activationValid => widget.appState.activationValid;

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
        () => TextEditingController(text: _stakeSpecialController.text.trim()),
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

  String _formatYuan(num? value) {
    if (value == null) return '-';
    return '${value.toStringAsFixed(0)}元';
  }

  @override
  void initState() {
    super.initState();
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

  Future<void> _loadLatestDraw() async {
    setState(() {
      _loading = true;
      _statusMessage = null;
      _pendingRecordId = null;
    });
    final draw = await _fetchLatestDraw();
    if (!mounted) return;
    if (draw == null) {
      setState(() {
        _loading = false;
        _statusMessage = '获取最新开奖失败';
      });
      return;
    }
    final nextPeriod = _computeNextPeriod(draw.id);
    setState(() {
      _loading = false;
      _latestDraw = draw;
      _nextPeriod = nextPeriod;
      _periodController.text =
          _periodController.text.trim().isEmpty ? nextPeriod : _periodController.text;
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
      setState(() => _manualBets = items);
    } catch (_) {
      if (!mounted) return;
      setState(() => _manualBets = []);
    } finally {
      if (mounted) {
        setState(() => _loadingBets = false);
      }
    }
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
                      Expanded(
                        child: Text(
                          '地区：${_region == 'hk' ? '香港' : '澳门'}',
                          style: const TextStyle(
                            fontWeight: FontWeight.bold,
                            fontSize: 16,
                          ),
                        ),
                      ),
                      SegmentedButton<String>(
                        segments: const [
                          ButtonSegment(value: 'hk', label: Text('香港')),
                          ButtonSegment(value: 'macau', label: Text('澳门')),
                        ],
                        selected: {_region},
                        onSelectionChanged: (value) {
                          setState(() {
                            _region = value.first;
                            _latestDraw = null;
                            _nextPeriod = '';
                            _periodController.clear();
                            _clearPending();
                          });
                          _loadLatestDraw();
                          _loadManualBets();
                        },
                      ),
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
                        onPressed: _loadLatestDraw,
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
                  const SizedBox(height: 10),
                  TextField(
                    controller: _bettorController,
                    onChanged: (_) => setState(_clearPending),
                    decoration: const InputDecoration(
                      labelText: '下注人（选填）',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    '下注类型',
                    style: TextStyle(fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 8),
                  SegmentedButton<String>(
                    segments: const [
                      ButtonSegment(value: 'number', label: Text('号码')),
                      ButtonSegment(value: 'zodiac', label: Text('生肖')),
                      ButtonSegment(value: 'color', label: Text('波色')),
                      ButtonSegment(value: 'parity', label: Text('单双')),
                    ],
                    selected: {_betType},
                    onSelectionChanged: (value) {
                      setState(() {
                        _betType = value.first;
                        _clearPending();
                      });
                    },
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
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: _zodiacOptions.map((zodiac) {
                        final selected = _selectedZodiacs.contains(zodiac);
                        return FilterChip(
                          label: Text(zodiac),
                          selected: selected,
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
                        );
                      }).toList(),
                    ),
                  ],
                  if (_betType == 'color') ...[
                    const SizedBox(height: 16),
                    const Text(
                      '波色下注',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: _colorOptions.map((color) {
                        final selected = _selectedColors.contains(color);
                        final chipColor = color == '红'
                            ? const Color(0xFFE54B4B)
                            : color == '蓝'
                                ? const Color(0xFF2D6CDF)
                                : const Color(0xFF36B37E);
                        return FilterChip(
                          label: Text(color),
                          selected: selected,
                          selectedColor: chipColor.withOpacity(0.15),
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
                        );
                      }).toList(),
                    ),
                  ],
                  if (_betType == 'parity') ...[
                    const SizedBox(height: 16),
                    const Text(
                      '单双下注',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: _parityOptions.map((parity) {
                        final selected = _selectedParity.contains(parity);
                        return FilterChip(
                          label: Text(parity),
                          selected: selected,
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
                        );
                      }).toList(),
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
                                        TextEditingController(text: '10');
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
                        const SizedBox(height: 8),
                        Row(
                          children: [
                            Expanded(
                              child: TextField(
                                controller: _stakeSpecialController,
                                onChanged: (_) => setState(_clearPending),
                                keyboardType:
                                    const TextInputType.numberWithOptions(decimal: true),
                                decoration: const InputDecoration(
                                  labelText: '默认金额',
                                  border: OutlineInputBorder(),
                                ),
                              ),
                            ),
                            const SizedBox(width: 8),
                            ElevatedButton(
                              onPressed: _selectedNumbers.isEmpty
                                  ? null
                                  : () {
                                      final value =
                                          _stakeSpecialController.text.trim();
                                      for (final controller
                                          in _numberStakeControllers.values) {
                                        controller.text = value;
                                      }
                                      setState(_clearPending);
                                    },
                              child: const Text('应用全部'),
                            ),
                          ],
                        ),
                        const SizedBox(height: 12),
                        TextField(
                          controller: _numberOddsController,
                          onChanged: (_) => setState(_clearPending),
                          keyboardType:
                              const TextInputType.numberWithOptions(decimal: true),
                          decoration: const InputDecoration(
                            labelText: '号码赔率',
                            border: OutlineInputBorder(),
                          ),
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
                    TextField(
                      controller: _zodiacOddsController,
                      onChanged: (_) => setState(_clearPending),
                      keyboardType:
                          const TextInputType.numberWithOptions(decimal: true),
                      decoration: const InputDecoration(
                        labelText: '生肖赔率',
                        border: OutlineInputBorder(),
                      ),
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
                    TextField(
                      controller: _colorOddsController,
                      onChanged: (_) => setState(_clearPending),
                      keyboardType:
                          const TextInputType.numberWithOptions(decimal: true),
                      decoration: const InputDecoration(
                        labelText: '波色赔率',
                        border: OutlineInputBorder(),
                      ),
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
                    TextField(
                      controller: _parityOddsController,
                      onChanged: (_) => setState(_clearPending),
                      keyboardType:
                          const TextInputType.numberWithOptions(decimal: true),
                      decoration: const InputDecoration(
                        labelText: '单双赔率',
                        border: OutlineInputBorder(),
                      ),
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
                      const SizedBox(width: 10),
                      Expanded(
                        child: ElevatedButton(
                          onPressed: _settling
                              ? null
                              : activationValid
                                  ? _settleBet
                                  : _promptActivation,
                          child: _settling
                              ? const SizedBox(
                                  width: 20,
                                  height: 20,
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                )
                              : const Text('计算盈亏'),
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

                        return Column(
                          children: orderedPeriods.map((period) {
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
                                color: item.win ? const Color(0xFF0B6B4F) : Colors.redAccent,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(width: 12),
                            Text(
                              _formatYuan(item.profit),
                              style: TextStyle(
                                color: item.profit >= 0
                                    ? const Color(0xFF0B6B4F)
                                    : Colors.redAccent,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      '总下注：${_formatYuan(_totalStake)}  盈亏：${_formatYuan(_totalProfit)}',
                      style: const TextStyle(fontWeight: FontWeight.bold),
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
  String _region = 'hk';
  bool _loading = false;
  final TextEditingController _yearController = TextEditingController();
  final TextEditingController _monthController = TextEditingController();
  final TextEditingController _periodController = TextEditingController();
  final TextEditingController _specialNumberController = TextEditingController();
  final TextEditingController _specialZodiacController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _yearController.text = DateTime.now().year.toString();
    _fetch();
  }

  @override
  void dispose() {
    _yearController.dispose();
    _monthController.dispose();
    _periodController.dispose();
    _specialNumberController.dispose();
    _specialZodiacController.dispose();
    super.dispose();
  }

  Future<void> _fetch() async {
    setState(() => _loading = true);
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
      if (mounted) {
        setState(() => _loading = false);
      }
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
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
          Container(
            margin: const EdgeInsets.all(16),
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                colors: [Color(0xFF0B6B4F), Color(0xFF0F9D58)],
              ),
              borderRadius: BorderRadius.circular(20),
              boxShadow: const [
                BoxShadow(
                  color: Color(0x22000000),
                  blurRadius: 12,
                  offset: Offset(0, 6),
                )
              ],
            ),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        '本年度开奖',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        _region == 'hk' ? '香港六合彩' : '澳门六合彩',
                        style: const TextStyle(color: Colors.white70),
                      ),
                    ],
                  ),
                ),
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(value: 'hk', label: Text('香港')),
                    ButtonSegment(value: 'macau', label: Text('澳门')),
                  ],
                  selected: {_region},
                  onSelectionChanged: (value) {
                    setState(() => _region = value.first);
                    _fetch();
                  },
                  style: ButtonStyle(
                    foregroundColor: WidgetStateProperty.all(Colors.white),
                    backgroundColor:
                        WidgetStateProperty.all(Colors.white24),
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
                OutlinedButton(
                  onPressed: _resetFilters,
                  child: const Text('重置'),
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
                              Wrap(
                                crossAxisAlignment: WrapCrossAlignment.center,
                                spacing: 8,
                                runSpacing: 8,
                                children: [
                                  const Text(
                                    '平码：',
                                    style: TextStyle(fontWeight: FontWeight.w600),
                                  ),
                                  ...record.normalNumbers.asMap().entries.map(
                                        (entry) => _NumberZodiacTile(
                                          number: entry.value,
                                          zodiac: normalZodiacs[entry.key],
                                          color: ballColor(entry.value),
                                        ),
                                      ),
                                  const SizedBox(width: 4),
                                  const Text(
                                    '特码：',
                                    style: TextStyle(fontWeight: FontWeight.w600),
                                  ),
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
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: outlined ? Colors.white : color,
        border: Border.all(color: color, width: 2),
      ),
      alignment: Alignment.center,
      child: Text(
        number,
        style: TextStyle(
          color: outlined ? color : Colors.white,
          fontWeight: FontWeight.bold,
          fontSize: fontSize,
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
  String _region = 'hk';
  String _strategy = 'hybrid';
  bool _loading = false;
  String _aiText = '';
  Map<String, dynamic>? _result;
  List<String> _normalZodiacs = [];
  String _specialZodiac = '';
  bool _loadingRecords = false;
  List<PredictionItem> _predictionRecords = [];
  final Map<int, List<String>> _recordNormalZodiacs = {};
  final Map<int, String> _recordSpecialZodiacs = {};
  StreamSubscription<Map<String, dynamic>>? _aiSubscription;

  final Map<String, String> _strategyLabels = const {
    'hybrid': '综合',
    'balanced': '均衡',
    'hot': '热门',
    'cold': '冷门',
    'trend': '走势',
    'random': '随机',
    'ai': 'AI智能',
  };

  LinearGradient? _strategyGradient(String key) {
    switch (key) {
      case 'random':
        return const LinearGradient(
          colors: [Color(0xFF28A745), Color(0xFF20C997)],
        );
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
    final borderColor = gradient?.colors.first ?? Colors.grey.shade400;
    final activationValid = widget.appState.activationValid;
    return GestureDetector(
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
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          decoration: BoxDecoration(
            gradient: selected ? gradient : null,
            color: selected ? null : Colors.white,
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: selected ? Colors.transparent : borderColor),
            boxShadow: selected
                ? [
                    BoxShadow(
                      color: borderColor.withOpacity(0.35),
                      blurRadius: 8,
                      offset: const Offset(0, 4),
                    ),
                  ]
                : [],
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (selected)
                const Padding(
                  padding: EdgeInsets.only(right: 6),
                  child: Icon(Icons.check, size: 16, color: Colors.white),
                ),
              Text(
                label,
                style: TextStyle(
                  color: selected ? Colors.white : Colors.black87,
                  fontWeight: FontWeight.w600,
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

  Future<void> _updateZodiacs(List<String> numbers) async {
    if (numbers.isEmpty) return;
    try {
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
          .predictAiStream(region: _region, year: _currentYear)
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
          await _loadPredictionRecords();
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
        _showMessage('AI流式失败: $e');
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
          year: _currentYear,
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
      await _loadPredictionRecords();
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

    final normalZodiacs = _normalZodiacs.length == cleanNormal.length
        ? _normalZodiacs
        : List.filled(cleanNormal.length, '');

    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
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

  Future<void> _loadPredictionRecords() async {
    setState(() => _loadingRecords = true);
    try {
      final res = await ApiClient.instance.predictions(
        page: 1,
        pageSize: 10,
        region: _region,
        includeZodiacs: true,
        year: _currentYear,
      );
      final items = (res['items'] as List<dynamic>? ?? [])
          .map((value) => PredictionItem.fromJson(value as Map<String, dynamic>))
          .toList();
      if (!mounted) return;
      setState(() {
        _predictionRecords = items;
        for (final item in items) {
          if (item.normalZodiacs.isNotEmpty) {
            _recordNormalZodiacs[item.id] = item.normalZodiacs;
          }
          if (item.specialZodiac.isNotEmpty) {
            _recordSpecialZodiacs[item.id] = item.specialZodiac;
          }
        }
      });
      for (final item in items) {
        if (!_recordNormalZodiacs.containsKey(item.id)) {
          await _loadRecordZodiacs(item);
        }
      }
    } catch (_) {
      if (!mounted) return;
      _showMessage('获取预测记录失败');
    } finally {
      if (mounted) {
        setState(() => _loadingRecords = false);
      }
    }
  }

  Future<void> _loadRecordZodiacs(PredictionItem item) async {
    if (_recordNormalZodiacs.containsKey(item.id)) return;
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

  String _resultLabel(String value) {
    switch (value) {
      case 'special_hit':
        return '中特码';
      case 'normal_hit':
        return '中平码';
      case 'wrong':
        return '未命中';
      default:
        return '待开奖';
    }
  }

  Color _resultColor(String value) {
    switch (value) {
      case 'special_hit':
        return const Color(0xFF0B6B4F);
      case 'normal_hit':
        return const Color(0xFF0F9D58);
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
        return const Color(0xFF43A047);
      case 'hybrid':
        return const Color(0xFF6A1B9A);
      case 'balanced':
        return const Color(0xFFFB8C00);
      case 'random':
        return const Color(0xFF00897B);
      case 'ai':
        return const Color(0xFF5E35B1);
      default:
        return Colors.black87;
    }
  }

  Widget _buildPredictionRecordItem(PredictionItem item) {
    final normalZodiacs = _recordNormalZodiacs[item.id] ??
        List.filled(item.normalNumbers.length, '');
    final specialZodiac = _recordSpecialZodiacs[item.id] ?? item.specialZodiac;
    final actualSpecialNumber = item.actualSpecialNumber;
    final actualSpecialZodiac = item.actualSpecialZodiac;
    final strategyLabel = _strategyLabels[item.strategy] ?? item.strategy;
    final createdAt = item.createdAt == null
        ? ''
        : '${item.createdAt!.year}-${item.createdAt!.month.toString().padLeft(2, '0')}-${item.createdAt!.day.toString().padLeft(2, '0')}';

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        boxShadow: const [
          BoxShadow(
            color: Color(0x14000000),
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
                  RichText(
                    text: TextSpan(
                      style: const TextStyle(
                        fontWeight: FontWeight.bold,
                        color: Colors.black87,
                      ),
                      children: [
                        TextSpan(text: '预测时间：$createdAt  策略：'),
                        TextSpan(
                          text: strategyLabel,
                          style: TextStyle(color: _strategyColor(item.strategy)),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: _resultColor(item.result).withOpacity(0.1),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(
                  _resultLabel(item.result),
                  style: TextStyle(
                    color: _resultColor(item.result),
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            crossAxisAlignment: WrapCrossAlignment.center,
            spacing: 8,
            runSpacing: 8,
            children: [
              const Text(
                '平码：',
                style: TextStyle(fontWeight: FontWeight.w600),
              ),
              ...item.normalNumbers.asMap().entries.map(
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
              const Text(
                '特码：',
                style: TextStyle(fontWeight: FontWeight.w600),
              ),
              if (item.specialNumber.isNotEmpty)
                _NumberZodiacTile(
                  number: item.specialNumber,
                  zodiac: specialZodiac,
                  color: ballColor(item.specialNumber),
                  outlined: true,
                  highlight: true,
                  ballSize: 36,
                  numberFontSize: 13,
                  zodiacFontSize: 11,
                  gap: 3,
                ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            actualSpecialNumber.isEmpty
                ? '开奖结果：未开奖'
                : '开奖结果：$actualSpecialNumber  生肖：$actualSpecialZodiac',
            style: TextStyle(
              color: actualSpecialNumber.isEmpty
                  ? Colors.orange
                  : Colors.grey.shade700,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPredictionRecordsSection() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(
                  child: Text(
                    '预测记录',
                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
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
                  for (final item in _predictionRecords) {
                    final period = item.period;
                    if (!grouped.containsKey(period)) {
                      grouped[period] = [];
                      orderedPeriods.add(period);
                    }
                    grouped[period]!.add(item);
                  }

                  return Column(
                    children: orderedPeriods.map((period) {
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
                            Text(
                              '期号：$period',
                              style: const TextStyle(
                                fontSize: 14,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(height: 8),
                            Column(
                              children: items
                                  .map(_buildPredictionRecordItem)
                                  .toList(),
                            ),
                          ],
                        ),
                      );
                    }).toList(),
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
                        Expanded(
                          child: SegmentedButton<String>(
                            segments: const [
                              ButtonSegment(value: 'hk', label: Text('香港')),
                              ButtonSegment(value: 'macau', label: Text('澳门')),
                            ],
                            selected: {_region},
                            onSelectionChanged: (value) {
                              setState(() => _region = value.first);
                              _loadPredictionRecords();
                            },
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: Wrap(
                        spacing: 8,
                        children: _strategyLabels.entries.map((entry) {
                          return _buildStrategyChip(entry.key, entry.value);
                        }).toList(),
                      ),
                    ),
                    const SizedBox(height: 16),
                    if (_loading)
                      const SizedBox(
                        height: 20,
                        width: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    else
                      Text(
                        '点击策略自动生成预测',
                        style: TextStyle(color: Colors.grey.shade600),
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
                  _aiText.isNotEmpty
                      ? Card(
                          child: Padding(
                            padding: const EdgeInsets.all(16),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                _buildPredictionNumbers(),
                                const Divider(),
                                Text(_aiText),
                              ],
                            ),
                          ),
                        )
                      : Card(
                          child: Padding(
                            padding: const EdgeInsets.all(16),
                            child: _buildPredictionNumbers(),
                          ),
                        ),
                  const SizedBox(height: 16),
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
  Map<String, dynamic>? _betSummary;
  bool _loadingBetSummary = false;

  @override
  void initState() {
    super.initState();
    _loadAccuracy();
    _loadVersion();
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
      setState(() => _appVersion = info.version);
    } catch (_) {}
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

  Widget _buildManualBetItem(Map<String, dynamic> item) {
    final status = item['status']?.toString() ?? 'pending';
    final bettor = item['bettor_name']?.toString() ?? '';
    final createdAt = item['created_at']?.toString() ?? '';
    final numbers = _formatNumberStakesText(
      item['selected_numbers']?.toString() ?? '',
    );
    final zodiacs = item['selected_zodiacs']?.toString() ?? '';
    final colors = item['selected_colors']?.toString() ?? '';
    final parity = item['selected_parity']?.toString() ?? '';
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
                      ? const Color(0xFFE8F5E9)
                      : const Color(0xFFFFF3E0),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  status == 'settled' ? '已结算' : '待结算',
                  style: TextStyle(
                    fontSize: 12,
                    color: status == 'settled'
                        ? const Color(0xFF0B6B4F)
                        : Colors.orange,
                  ),
                ),
              ),
            ],
          ),
          if (numbers.isNotEmpty) Text('号码：$numbers'),
          if (bettor.isNotEmpty) Text('下注人：$bettor'),
          if (zodiacs.isNotEmpty) Text('生肖：$zodiacs'),
          if (colors.isNotEmpty) Text('波色：$colors'),
          if (parity.isNotEmpty) Text('单双：$parity'),
          if (status == 'settled')
            Text('开奖结果：$special  生肖：$specialZodiac'),
          Text(
            '下注：${_formatYuan(stake)}  盈亏：${_formatYuan(profit)}',
          ),
        ],
      ),
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
                  Text(
                    user.username,
                    style: const TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(user.email),
                  const SizedBox(height: 8),
                  Row(
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
                                ? Colors.green.shade100
                                : activationExpired
                                    ? Colors.red.shade100
                                    : Colors.orange.shade100,
                      ),
                      const SizedBox(width: 8),
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
                          ],
                        ),
                        const SizedBox(height: 12),
                        const Text(
                          '预测准确率',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 8),
                        if (_overall != null) ...[
                          Text('总体命中率：${_overall!.accuracy}%'),
                          Text('总预测次数：${_overall!.total}'),
                          Text('特码命中：${_overall!.specialHits}'),
                          Text('平码命中：${_overall!.normalHits}'),
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
                        Row(
                          children: [
                            const Expanded(
                              child: Text(
                                '盈亏报表',
                                style: TextStyle(
                                  fontSize: 16,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                            ),
                            IconButton(
                              onPressed:
                                  _loadingBetSummary ? null : _loadBetSummary,
                              icon: const Icon(Icons.refresh),
                            ),
                          ],
                        ),
                        const SizedBox(height: 8),
                        if (_betSummary != null) ...[
                          Text('已结算：${_betSummary!['settled_count'] ?? 0}'),
                          Text('待结算：${_betSummary!['pending_count'] ?? 0}'),
                          Text(
                            '总下注：${_formatYuan((_betSummary!['total_stake'] as num?) ?? 0)}',
                          ),
                          Text(
                            '总盈亏：${_formatYuan((_betSummary!['total_profit'] as num?) ?? 0)}',
                          ),
                          Text(
                            '赢/输/平：${_betSummary!['win_count'] ?? 0}/'
                            '${_betSummary!['lose_count'] ?? 0}/'
                            '${_betSummary!['draw_count'] ?? 0}',
                          ),
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
            child: ListTile(
              leading: const Icon(Icons.lock_outline),
              title: const Text('修改密码'),
              subtitle: const Text('更新登录密码'),
              trailing: const Icon(Icons.chevron_right),
              onTap: _showChangePasswordDialog,
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
  static const String _proxy = 'https://docker.071717.xyz/';

  static Future<void> checkForUpdate(BuildContext context) async {
    try {
      final info = await PackageInfo.fromPlatform();
      final currentVersion = info.version;
      final latest = await _fetchLatestRelease();
      if (latest == null) return;

      final latestVersion = latest.version;
      if (_compareVersions(latestVersion, currentVersion) <= 0) {
        return;
      }

      if (!context.mounted) return;
      final shouldUpdate = await showDialog<bool>(
            context: context,
            builder: (dialogContext) => AlertDialog(
              title: const Text('发现新版本'),
              content: Text('最新版本 $latestVersion，是否立即更新？'),
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
                Text('${(progress * 100).toStringAsFixed(0)}元'),
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








