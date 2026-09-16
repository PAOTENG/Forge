@echo off
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=8080 >nul 2>&1
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=8080 connectaddress=127.0.0.1 connectport=5173
netsh advfirewall firewall delete rule name=ForgeVite5173 >nul 2>&1
netsh advfirewall firewall delete rule name=ForgeVite8080 >nul 2>&1
netsh advfirewall firewall add rule name=ForgeVite5173 dir=in action=allow protocol=TCP localport=5173
netsh advfirewall firewall add rule name=ForgeVite8080 dir=in action=allow protocol=TCP localport=8080
echo.
echo portproxy:
netsh interface portproxy show all
echo.
echo Done. Phone should open http://192.168.137.1:8080/
pause
