@echo off
rem  setlocal scopes the working directory: this script does `cd /d` to find its
rem  own sources, and without setlocal that change persists into whatever
rem  `call`ed it. install.bat then looked for selftest.py in this folder and
rem  reported a failed install right after a successful build.
setlocal
rem Build the Java reference strategy.
rem
rem javac and jar directly -- no Maven, no Gradle. A protocol that needs a build
rem system to join is not a protocol any language can implement.
cd /d "%~dp0"
where javac >nul 2>&1 || (echo   no JDK on PATH & exit /b 1)
if exist build rmdir /s /q build
javac -d build Json.java OrbStrategy.java || exit /b 1
jar --create --file orb.jar --main-class OrbStrategy -C build . || exit /b 1
echo   built orb.jar
java -jar orb.jar --describe >nul || (echo   jar does not run & exit /b 1)
echo   --describe OK
exit /b 0
