@echo off
echo.
echo    ##########################################
echo    #     تشغيل أداة مراقبة MikroTik       #
echo    #      اضغط على أي مفتاح للإغلاق        #
echo    ##########################################
echo.
echo تأكد من تثبيت Python وتشغيله...
echo.
cd /d %~dp0
python app.py
pause