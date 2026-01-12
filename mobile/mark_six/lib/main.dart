
import 'dart:async';

import 'package:flutter/material.dart';

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
          NavigationDestination(icon: Icon(Icons.auto_graph), label: '开奖预测'),
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

class RecordsScreen extends StatefulWidget {
  const RecordsScreen({super.key});

  @override
  State<RecordsScreen> createState() => _RecordsScreenState();
}

class _RecordsScreenState extends State<RecordsScreen> {
  List<DrawRecord> _records = [];
  String _region = 'hk';
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    setState(() => _loading = true);
    try {
      final year = DateTime.now().year.toString();
      final raw = await ApiClient.instance.draws(region: _region, year: year);
      final records = raw
          .map((item) => DrawRecord.fromJson(item as Map<String, dynamic>))
          .take(10)
          .toList();
      setState(() => _records = records);
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
                        '近10期开奖',
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
                                '${record.id}  |  ${record.date}',
                                style: const TextStyle(
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              const SizedBox(height: 12),
                              Wrap(
                                spacing: 8,
                                runSpacing: 8,
                                children: [
                                  ...record.normalNumbers.asMap().entries.map(
                                        (entry) => _NumberZodiacTile(
                                          number: entry.value,
                                          zodiac: normalZodiacs[entry.key],
                                          color: ballColor(entry.value),
                                        ),
                                      ),
                                  _NumberZodiacTile(
                                    number: record.specialNumber,
                                    zodiac: specialZodiac,
                                    color: ballColor(record.specialNumber),
                                    outlined: true,
                                  ),
                                ],
                              ),
                              if (specialZodiac.isNotEmpty)
                                Padding(
                                  padding: const EdgeInsets.only(top: 8),
                                  child: Text(
                                    '特码生肖：$specialZodiac',
                                    style: TextStyle(color: Colors.grey.shade600),
                                  ),
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
  });

  final String number;
  final String zodiac;
  final Color color;
  final bool outlined;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        _Ball(number: number, color: color, outlined: outlined),
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
      width: 38,
      height: 38,
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
  final TextEditingController _year = TextEditingController();
  String _region = 'hk';
  String _strategy = 'hybrid';
  bool _loading = false;
  String _aiText = '';
  Map<String, dynamic>? _result;
  List<String> _normalZodiacs = [];
  String _specialZodiac = '';
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

  @override
  void initState() {
    super.initState();
    _year.text = DateTime.now().year.toString();
  }

  @override
  void dispose() {
    _aiSubscription?.cancel();
    _year.dispose();
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
        year: _year.text.trim(),
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
          .predictAiStream(region: _region, year: _year.text.trim())
          .listen((event) async {
        if (!mounted) return;
        if (event['type'] == 'content') {
          setState(() {
            _aiText += event['content']?.toString() ?? '';
          });
        } else if (event['type'] == 'done') {
          final normal = (event['normal'] as List<dynamic>? ?? [])
              .map((value) => value.toString())
              .toList();
          final special = (event['special'] as Map<String, dynamic>? ?? {});
          final specialNumber = special['number']?.toString() ?? '';
          final numbers = [...normal, specialNumber]
              .where((n) => n.isNotEmpty)
              .toList();
          setState(() {
            _result = event;
            _loading = false;
          });
          await _updateZodiacs(numbers);
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

    try {
      final res = await ApiClient.instance.predict(
        region: _region,
        strategy: _strategy,
        year: _year.text.trim(),
      );
      final normal = (res['normal'] as List<dynamic>? ?? [])
          .map((value) => value.toString())
          .toList();
      final special = res['special'] as Map<String, dynamic>? ?? {};
      final specialNumber = special['number']?.toString() ?? '';
      final numbers = [...normal, specialNumber]
          .where((n) => n.isNotEmpty)
          .toList();
      if (!mounted) return;
      setState(() {
        _result = res;
        _loading = false;
      });
      await _updateZodiacs(numbers);
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      _showMessage('预测失败: $e');
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  Widget _buildPredictionNumbers() {
    final normal = (_result?['normal'] as List<dynamic>? ?? [])
        .map((value) => value.toString())
        .toList();
    final specialMap = _result?['special'] as Map<String, dynamic>? ?? {};
    final specialNumber = specialMap['number']?.toString() ?? '';
    if (normal.isEmpty && specialNumber.isEmpty) {
      return const Text('暂无预测结果');
    }

    final normalZodiacs = _normalZodiacs.length == normal.length
        ? _normalZodiacs
        : List.filled(normal.length, '');

    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        ...normal.asMap().entries.map(
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
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final active = widget.appState.user?.isActive ?? false;

    return Scaffold(
      appBar: AppBar(title: const Text('开奖预测')),
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
                            },
                          ),
                        ),
                        const SizedBox(width: 12),
                        SizedBox(
                          width: 110,
                          child: TextField(
                            controller: _year,
                            decoration: const InputDecoration(labelText: '年份'),
                            keyboardType: TextInputType.number,
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
                          final selected = _strategy == entry.key;
                          return ChoiceChip(
                            label: Text(entry.value),
                            selected: selected,
                            onSelected: (_) {
                              setState(() => _strategy = entry.key);
                            },
                          );
                        }).toList(),
                      ),
                    ),
                    const SizedBox(height: 16),
                    SizedBox(
                      width: double.infinity,
                      child: ElevatedButton(
                        onPressed: _loading ? null : _handlePredict,
                        child: _loading
                            ? const SizedBox(
                                height: 20,
                                width: 20,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Text('生成预测'),
                      ),
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
              child: _aiText.isNotEmpty
                  ? Card(
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            _buildPredictionNumbers(),
                            const Divider(),
                            Expanded(
                              child: SingleChildScrollView(
                                child: Text(_aiText),
                              ),
                            ),
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
            ),
          ],
        ),
      ),
    );
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

  @override
  void initState() {
    super.initState();
    _loadAccuracy();
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
