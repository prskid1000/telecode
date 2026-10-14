@echo off
rem Randomised invariant test of the MoE cache planner (patches/llama.cpp 0003): no GPU, no model.
rem Needs a built llama.cpp tree (ggml-base.lib) at build\llama.cpp\build-telecode, see llamacpp/patcher.py.
rem Usage: tools\moe_cache_lru_fuzz.bat   (prints "OK: N steps, 0 failures", or the first broken invariant)
setlocal
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
set HERE=%~dp0
set LLAMA=%HERE%..\build\llama.cpp
if not exist "%LLAMA%\build-telecode\ggml\src\ggml-base.lib" (echo build the patched llama.cpp first & exit /b 2)
set OUT=%TEMP%\moe_cache_lru_fuzz
if not exist "%OUT%" mkdir "%OUT%"
pushd "%LLAMA%"
cl /nologo /EHsc /std:c++17 /O1 /MD /I src /I include /I ggml\include /I ggml\src /I common "%HERE%moe_cache_lru_fuzz.cpp" /Fe:"%OUT%\fuzz.exe" /Fo:"%OUT%\\" /link /FORCE:UNRESOLVED build-telecode\ggml\src\ggml-base.lib >nul
popd
"%OUT%\fuzz.exe"
