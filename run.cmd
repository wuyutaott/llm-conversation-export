@echo off
rem Windows 入口。跨平台编排逻辑统一在 run.py。
rem %~dp0 = 本脚本所在目录（带反斜杠结尾），保证从任意目录双击/调用都能定位仓库根。
setlocal
python "%~dp0run.py" %*
endlocal
