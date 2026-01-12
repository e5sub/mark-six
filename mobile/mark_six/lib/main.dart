
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
      title: '??????',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF0B6B4F),
          secondary: const Color(0xFFF4B547),
        ),
        fontFamily: 'NotoSansSC',
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
      return res['message']?.toString() ?? '????';
    } catch (e) {
      return '????: $e';
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
      return res['message']?.toString() ?? '????';
    } catch (e) {
      return '????: $e';
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
      return res['message']?.toString() ?? '????';
    } catch (e) {
      return '????: $e';
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
                      '??????',
                      style:
                          TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      '???????????',
                      style: TextStyle(color: Colors.grey.shade600),
                    ),
                    const SizedBox(height: 24),
                    TextField(
                      controller: _username,
                      decoration: const InputDecoration(labelText: '??????'),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _password,
                      decoration: const InputDecoration(labelText: '??'),
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
                            : const Text('??'),
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
                      child: const Text('?????????'),
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
        title: const Text('????'),
        content: const Text('??????????????'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('???'),
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
      appBar: AppBar(title: const Text('????')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            TextField(
              controller: _username,
              decoration: const InputDecoration(labelText: '???'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _email,
              decoration: const InputDecoration(labelText: '??'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _password,
              decoration: const InputDecoration(labelText: '??'),
              obscureText: true,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _confirmPassword,
              decoration: const InputDecoration(labelText: '????'),
              obscureText: true,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _inviteCode,
              decoration: const InputDecoration(
                labelText: '???????',
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
                    : const Text('??'),
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
          NavigationDestination(icon: Icon(Icons.list_alt), label: '????'),
          NavigationDestination(icon: Icon(Icons.auto_graph), label: '????'),
          NavigationDestination(icon: Icon(Icons.person), label: '????'),
        ],
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
      _showMessage('????????');
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

  Color _ballColor(String number) {
    final numValue = int.tryParse(number) ?? 0;
    const red = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46];
    const blue = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48];
    if (red.contains(numValue)) return const Color(0xFFE54B4B);
    if (blue.contains(numValue)) return const Color(0xFF2D6CDF);
    return const Color(0xFF36B37E);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('????')),
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
                        '?10???',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        _region == 'hk' ? '?????' : '?????',
                        style: const TextStyle(color: Colors.white70),
                      ),
                    ],
                  ),
                ),
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(value: 'hk', label: Text('??')),
                    ButtonSegment(value: 'macau', label: Text('??')),
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
                                  ...record.normalNumbers.map(
                                    (number) => _Ball(number: number, color: _ballColor(number)),
                                  ),
                                  _Ball(
                                    number: record.specialNumber,
                                    color: _ballColor(record.specialNumber),
                                    outlined: true,
                                  ),
                                ],
                              ),
                              if (record.specialZodiac.isNotEmpty)
                                Padding(
                                  padding: const EdgeInsets.only(top: 8),
                                  child: Text(
                                    '?????${record.specialZodiac}',
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
  StreamSubscription<Map<String, dynamic>>? _aiSubscription;

  final Map<String, String> _strategyLabels = const {
    'hybrid': '??',
    'balanced': '??',
    'hot': '??',
    'cold': '??',
    'trend': '??',
    'random': '??',
    'ai': 'AI??',
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

  Future<void> _handlePredict() async {
    setState(() {
      _loading = true;
      _result = null;
      _aiText = '';
    });

    if (_strategy == 'ai') {
      _aiSubscription?.cancel();
      _aiSubscription = ApiClient.instance
          .predictAiStream(region: _region, year: _year.text.trim())
          .listen((event) {
        if (!mounted) return;
        if (event['type'] == 'content') {
          setState(() {
            _aiText += event['content']?.toString() ?? '';
          });
        } else if (event['type'] == 'done') {
          setState(() {
            _result = event;
            _loading = false;
          });
        } else if (event['type'] == 'error') {
          setState(() {
            _loading = false;
          });
          _showMessage(event['error']?.toString() ?? 'AI????');
        }
      }, onError: (e) {
        if (!mounted) return;
        setState(() => _loading = false);
        _showMessage('AI????: $e');
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
      if (!mounted) return;
      setState(() {
        _result = res;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      _showMessage('????: $e');
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final active = widget.appState.user?.isActive ?? false;
    final normal = _result?['normal'] as List<dynamic>?;
    final special = _result?['special'] as Map<String, dynamic>?;
    final resultSummary = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (normal != null) Text('???${normal.join(', ')}'),
        if (special != null) Text('???${special['number']}'),
        if ((_result?['recommendation_text'] as String?)?.isNotEmpty ?? false)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(_result?['recommendation_text'] as String),
          ),
      ],
    );

    return Scaffold(
      appBar: AppBar(title: const Text('????')),
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
                              ButtonSegment(value: 'hk', label: Text('??')),
                              ButtonSegment(value: 'macau', label: Text('??')),
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
                            decoration: const InputDecoration(labelText: '??'),
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
                            : const Text('????'),
                      ),
                    ),
                    if (!active)
                      const Padding(
                        padding: EdgeInsets.only(top: 8),
                        child: Text(
                          '?????????????????',
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
                            resultSummary,
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
                        child: resultSummary,
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
      _showMessage('???????');
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
        title: const Text('????'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(labelText: '???'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('??'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(controller.text.trim()),
            child: const Text('??'),
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
      appBar: AppBar(title: const Text('????')),
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
                        label: Text(user.isActive ? '???' : '???'),
                        backgroundColor:
                            user.isActive ? Colors.green.shade100 : Colors.orange.shade100,
                      ),
                      const SizedBox(width: 8),
                      if (user.activationExpiresAt != null)
                        Text('???${user.activationExpiresAt}'),
                    ],
                  ),
                  if (!user.isActive)
                    Padding(
                      padding: const EdgeInsets.only(top: 12),
                      child: ElevatedButton(
                        onPressed: () => _activate(context),
                        child: const Text('?????'),
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
                          '?????',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 8),
                        if (_overall != null) ...[
                          Text('??????${_overall!.accuracy}%'),
                          Text('??????${_overall!.total}'),
                          Text('?????${_overall!.specialHits}'),
                          Text('?????${_overall!.normalHits}'),
                        ] else
                          const Text('????'),
                      ],
                    ),
            ),
          ),
          const SizedBox(height: 16),
          OutlinedButton(
            onPressed: () => widget.appState.logout(),
            child: const Text('????'),
          ),
        ],
      ),
    );
  }
}
