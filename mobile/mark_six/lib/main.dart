
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
      appBar: AppBar(title: const Text('生肖号码')),
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
                    padding: const EdgeInsets.all(6),
                    physics: const AlwaysScrollableScrollPhysics(),
                    gridDelegate:
                        const SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: 4,
                      mainAxisSpacing: 6,
                      crossAxisSpacing: 6,
                      childAspectRatio: 1.0,
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
                            _Ball(number: item.number, color: color),
                            const SizedBox(height: 6),
                            Text(
                              item.zodiac,
                              style: TextStyle(
                                fontSize: 13,
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
  });

  final String number;
  final String zodiac;
  final Color color;
  final bool outlined;
  final bool highlight;

  @override
  Widget build(BuildContext context) {
    final ball = _Ball(number: number, color: color, outlined: outlined);
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
        const SizedBox(height: 4),
        Text(
          zodiac,
          style: TextStyle(fontSize: 12, color: Colors.grey.shade700),
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
  });

  final String number;
  final Color color;
  final bool outlined;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 46,
      height: 46,
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
          fontSize: 16,
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
    return GestureDetector(
      onTap: () {
        if (_loading) return;
        setState(() => _strategy = key);
        _handlePredict();
      },
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
              ),
            ),
        if (specialNumber.isNotEmpty)
          _NumberZodiacTile(
            number: specialNumber,
            zodiac: _specialZodiac,
            color: ballColor(specialNumber),
            outlined: true,
            highlight: true,
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
      );
      final items = (res['items'] as List<dynamic>? ?? [])
          .map((value) => PredictionItem.fromJson(value as Map<String, dynamic>))
          .toList();
      if (!mounted) return;
      setState(() {
        _predictionRecords = items;
      });
      for (final item in items) {
        await _loadRecordZodiacs(item);
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
                  '期号：${item.period}  预测时间：$createdAt  策略：$strategyLabel',
                  style: const TextStyle(fontWeight: FontWeight.bold),
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
              Column(
                children: _predictionRecords
                    .map(_buildPredictionRecordItem)
                    .toList(),
              ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final active = widget.appState.user?.isActive ?? false;

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
                    if (!active)
                      const Padding(
                        padding: EdgeInsets.only(top: 8),
                        child: Text(
                          '账号未激活，预测记录可能无法保存。',
                          style: TextStyle(color: Colors.orange),
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

  @override
  void initState() {
    super.initState();
    _loadAccuracy();
    _loadVersion();
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

  Future<void> _activate(BuildContext context) async {
    final controller = TextEditingController();
    final result = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('激活账号'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(labelText: '激活码'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(controller.text.trim()),
            child: const Text('激活'),
          ),
        ],
      ),
    );

    if (result != null && result.isNotEmpty) {
      final error = await widget.appState.activate(result);
      if (context.mounted && error != null) {
        _showMessage(error);
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
    final user = widget.appState.user;
    if (user == null) {
      return const SizedBox.shrink();
    }

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
                        label: Text(user.isActive ? '已激活' : '未激活'),
                        backgroundColor:
                            user.isActive ? Colors.green.shade100 : Colors.orange.shade100,
                      ),
                      const SizedBox(width: 8),
                      if (user.activationExpiresAt != null)
                        Text('到期：${user.activationExpiresAt}'),
                    ],
                  ),
                  if (!user.isActive)
                    Padding(
                      padding: const EdgeInsets.only(top: 12),
                      child: ElevatedButton(
                        onPressed: () => _activate(context),
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
