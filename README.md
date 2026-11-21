# VLESS REALITY для Happ

Комплект запускает Xray-core в Docker на Linux VPS и создаёт одну ссылку `vless://` для Happ. Профиль использует VLESS поверх TCP с REALITY и XTLS Vision. Своего домена и TLS-сертификата для VPS не требуется.

## Развёртывание через GitHub Actions

Распакуйте архив и поместите **содержимое** папки `happ-vless`, включая скрытую папку `.github`, в корень GitHub-репозитория. Workflow запускается при изменении файлов деплоя в ветке `main`. Ручной запуск доступен в **Actions → Deploy VLESS to VPS → Run workflow** для той же ветки.

### SSH-доступ на VPS

На VPS нужны Docker Engine, Python 3.9+, Bash и `flock` из пакета `util-linux`. Включите SSH и убедитесь, что пользователь деплоя может выполнить `docker info` без `sudo`. Для существующей установки используйте того же пользователя и каталог `~/happ-vless`. В этом каталоге сохраняются `state`, действующий UUID и ключи REALITY. Docker-порт откройте в сетевом экране VPS заранее.

Создайте отдельный SSH-ключ для CI на своём компьютере:

```bash
ssh-keygen -t ed25519 -N '' -f ~/.ssh/happ-vless-ci -C happ-vless-github-actions
ssh -p 22 deploy@VPS_HOST 'umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys' < ~/.ssh/happ-vless-ci.pub
```

Замените `deploy`, `VPS_HOST` и `22` на свои данные. Для отдельного ключа в `authorized_keys` можно добавить префикс `restrict`. Он запрещает проброс портов, agent forwarding и выделение терминала, сохраняя выполнение SSH-команд и передачу файлов.

Для проверки подлинности сервера получите его публичный SSH-ключ из доверенной консоли провайдера или уже проверенной SSH-сессии:

```bash
printf 'happ-vless-vps %s\n' "$(sudo cut -d ' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"
```

### Настройки репозитория

Откройте **Settings → Secrets and variables → Actions**.

| Secrets | Значение |
| --- | --- |
| `VPN_SSH_PRIVATE_KEY` | Полное содержимое `~/.ssh/happ-vless-ci`, включая первую и последнюю строки. |
| `VPN_SSH_KNOWN_HOSTS` | Строка из команды выше, начиная с `happ-vless-vps ssh-ed25519`. |

| Variables | Значение |
| --- | --- |
| `VPN_SSH_HOST` | IPv4 или DNS-имя VPS для SSH. Обязательно. |
| `VPN_SSH_USER` | Пользователь SSH с доступом к Docker. Обязательно. |
| `VPN_SSH_PORT` | SSH-порт. По умолчанию `22`. |
| `VPN_PUBLIC_HOST` | Публичный IPv4 или DNS-имя для Happ. По умолчанию совпадает с `VPN_SSH_HOST`. |
| `VPN_PORT` | Публичный TCP-порт VLESS. По умолчанию `443`. |
| `VPN_SNI` | Имя цели REALITY. По умолчанию `www.microsoft.com`. |
| `VPN_NAME` | Имя в Happ. По умолчанию `Мой VPN`. |

Если профиль уже есть, заполните переменные теми же публичным адресом, портом, SNI и названием, с которыми он был создан. При смене версии образа в `happ-vless.py` деплой сначала проверяет конфиг новым Xray, потом перезапускает контейнер. Если новый контейнер не проходит проверку запуска, установщик восстанавливает предыдущий.

### Получение ссылки для Happ

После успешного CI-запуска выполните на VPS от имени пользователя деплоя:

```bash
python3 ~/happ-vless/happ-vless.py link
```

Workflow проверяет состояние контейнера и доступность TCP-порта с VPS. Импортируйте полученную ссылку в Happ и проверьте соединение из внешней сети. Ссылка не выводится в лог Actions.

## Ручная установка

Понадобятся Linux VPS с публичным IPv4, рабочий Docker Engine, Python 3.9+ и свободный входящий TCP-порт. Скрипт использует официальный образ `ghcr.io/xtls/xray-core:26.11.9`. При первом запуске Docker скачает его из GHCR.

1. Перенесите `happ-vless.zip` на VPS, распакуйте и войдите в папку.
2. Разрешите входящий `443/tcp` в сетевом экране провайдера VPS. Если у сервера есть отдельный сетевой экран, учитывайте правила публикации портов Docker.
3. Запустите команды на VPS. Замените `203.0.113.10` на публичный IPv4 своего VPS или на домен, A-запись которого указывает на этот IPv4.

```bash
unzip happ-vless.zip
cd happ-vless
sudo python3 happ-vless.py install 203.0.113.10
```

Если Docker доступен вашему пользователю, можете выполнять все команды без `sudo`. Используйте тот же способ в последующих запусках.

Если `443/tcp` уже занят, при первой установке укажите свободный порт, например `--port 8443`, и откройте его в сетевом экране провайдера. Для своего названия профиля добавьте `--name "Мой VPS"`.

## Подключение в Happ

1. Скопируйте всю строку, начинающуюся с `vless://`. Ссылка даёт доступ к вашему серверу.
2. В Happ нажмите **+** и выберите добавление из буфера обмена.
3. Выберите появившийся профиль и подключитесь. Чтобы проверить маршрутизацию, откройте через Happ сайт определения внешнего IP и сравните адрес с публичным IPv4 VPS.

## Обслуживание

```bash
sudo python3 happ-vless.py link
sudo docker logs --tail 100 happ-vless
sudo docker restart happ-vless
sudo docker stop happ-vless
sudo docker start happ-vless
```

Команда `install` с теми же параметрами повторно выдаёт ту же ссылку, а при необходимости запускает остановленный контейнер. Стратегия Docker `unless-stopped` запускает контейнер после перезагрузки VPS, если он ранее не был остановлен вручную. Папку `state` храните вместе с установщиком. В ней находятся серверный ключ, настройки и клиентская ссылка.

## Если Happ не подключается

- Убедитесь, что в ссылке указан публичный адрес VPS и нужный порт, а входящий TCP разрешён в сетевом экране провайдера. Проверяйте из внешней сети.
- Проверьте состояние и логи контейнера командами `sudo docker ps -a --filter name=happ-vless` и `sudo docker logs --tail 100 happ-vless`.
- По умолчанию целью REALITY служит `www.microsoft.com:443`. VPS должен иметь доступ к ней по TCP и поддерживать корректное системное время. При первой установке цель можно выбрать через `--sni имя.домена`. Выбранный HTTPS-сервер должен поддерживать TLS 1.3 и HTTP/2. Имя цели REALITY и публичный адрес VPS задаются отдельно.

## Ссылки

- [Документация Xray REALITY](https://xtls.github.io/en/config/transports/reality.html)
- [Официальный Docker-образ Xray-core](https://github.com/XTLS/Xray-core/pkgs/container/xray-core)
- [Импорт конфигурации Happ](https://www.happ.su/main/faq/adding-configuration-subscription)
- [Публикация портов Docker](https://docs.docker.com/engine/network/port-publishing/)
- [Синтаксис GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
