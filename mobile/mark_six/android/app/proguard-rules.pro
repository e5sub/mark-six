# Keep Flutter plugin registrant classes used by reflection during startup.
-keep class io.flutter.plugins.** { *; }
-keep class io.flutter.plugin.** { *; }
-keep class io.flutter.embedding.** { *; }

# Keep app entry point and generated plugin wiring.
-keep class com.caiya.mark_six.MainActivity { *; }
-keep class io.flutter.plugins.GeneratedPluginRegistrant { *; }
