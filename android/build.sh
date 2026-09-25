#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
SDK="${ANDROID_SDK_ROOT:-$HOME/.local/share/android-sdk}"
BT="$SDK/build-tools/36.0.0"
JAR="$SDK/platforms/android-36/android.jar"
mkdir -p build/classes build/dex build/tests
javac --release 8 -d build/tests app/src/main/java/com/agentbridge/kalshihud/Rules.java tests/RulesTest.java
java -ea -cp build/tests RulesTest
javac --release 8 -classpath "$JAR" -d build/classes app/src/main/java/com/agentbridge/kalshihud/*.java
jar cf build/classes.jar -C build/classes .
"$BT/d8" --lib "$JAR" --min-api 26 --output build/dex build/classes.jar
rm -rf build/res && mkdir -p build/res
"$BT/aapt2" compile --dir app/src/main/res -o build/res/resources.zip
"$BT/aapt2" link -I "$JAR" --manifest app/src/main/AndroidManifest.xml build/res/resources.zip -o build/unsigned.apk
(cd build/dex && zip -q -u ../unsigned.apk classes.dex)
"$BT/zipalign" -f 4 build/unsigned.apk build/aligned.apk
KEY="${HUD_SIGNING_KEY:-$HOME/.local/share/kalshi-hud-signing/development.p12}"
if [ ! -f "$KEY" ]; then
 mkdir -p "$(dirname "$KEY")"
 keytool -genkeypair -keystore "$KEY" -storepass android -keypass android -alias hud -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Kalshi HUD development" >/dev/null 2>&1
 chmod 600 "$KEY"
fi
"$BT/apksigner" sign --ks "$KEY" --ks-pass pass:android --key-pass pass:android --out build/kalshi-paper-hud.apk build/aligned.apk
"$BT/apksigner" verify build/kalshi-paper-hud.apk
sha256sum build/kalshi-paper-hud.apk
