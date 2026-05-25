# Mark Six Flutter App

## Quick start

```bash
flutter pub get
flutter run
```

## Backend

Base URL is defined in `lib/config.dart`.

## Release signing (GitHub Actions)

Add these repository secrets:

- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_ALIAS`
- `ANDROID_KEY_PASSWORD`

The workflow is `Android Release APK`.
