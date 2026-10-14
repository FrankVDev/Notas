# Instalar Notas en un servidor Rocky Linux

Esta guía deja un servidor Rocky Linux 9 sirviendo la app en `http://notas.home.arpa` para toda la red de casa, administrado por SSH desde otra PC y preparado para alojar varios proyectos.

El servidor queda organizado así:

```
/opt/infra/              repositorio servidor-casa: Caddy (proxy inverso) y AdGuard Home (DNS)
/opt/proyectos/notas/    este repositorio
/opt/proyectos/...       otros proyectos, cada uno en su carpeta
```

```
dispositivo ──► AdGuard Home: "notas.home.arpa es 192.168.1.50"
dispositivo ──► Caddy :80/:443 ──red "proxy"──► notas-web ──► api ──► db
```

En los ejemplos, el servidor tiene la IP `192.168.1.50` y tu usuario se llama `frank`. Cámbialos por los tuyos.

---

## 1. Primeros pasos (con pantalla y teclado, una sola vez)

Durante la instalación de Rocky, crea tu usuario y marca **"Hacer este usuario administrador"** (lo agrega al grupo `wheel`, que puede usar `sudo`). Después de instalar:

```bash
sudo dnf -y update
sudo systemctl enable --now sshd
```

### IP fija

El servidor necesita una IP que no cambie. Se configura en el propio servidor, sin tocar el router:

```bash
nmcli connection show                       # anota el NOMBRE de tu conexión
sudo nmcli connection modify "NOMBRE" ipv4.method manual \
  ipv4.addresses 192.168.1.50/24 ipv4.gateway 192.168.1.1 ipv4.dns 192.168.1.1
sudo nmcli connection up "NOMBRE"
```

Elige una IP de tu red que esté **fuera del rango que reparte el router** para que no choque con otro dispositivo. Desde aquí, el servidor puede quedarse sin pantalla.

---

## 2. Acceso remoto por SSH

### Conectarte desde Windows

Windows 10 y 11 traen SSH. En PowerShell:

```powershell
ssh frank@192.168.1.50
```

### Entrar con llave en lugar de contraseña

En tu PC (no en el servidor):

```powershell
ssh-keygen -t ed25519
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh frank@192.168.1.50 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && restorecon -R ~/.ssh"
```

`ssh-keygen` ofrece una frase de protección para la llave: es opcional pero recomendable. El `restorecon` ajusta las etiquetas de SELinux; sin él, Rocky puede ignorar la llave.

**Si usas PuTTY:** genera la llave con PuTTYgen (guárdala como `.ppk` en tu PC) y copia al servidor la línea del recuadro *"Public key for pasting into OpenSSH authorized_keys file"*. Al servidor le da igual el programa: solo guarda la parte pública.

### Atajo

Crea `C:\Users\TU_USUARIO\.ssh\config`:

```
Host notas
    HostName 192.168.1.50
    User frank
```

Desde ahora basta con `ssh notas`.

### Cerrar el acceso por contraseña

Solo después de comprobar que la llave funciona:

```bash
sudo groupadd ssh-users
sudo usermod -aG ssh-users frank
sudo tee /etc/ssh/sshd_config.d/50-seguridad.conf << 'EOF'
PermitRootLogin no
PasswordAuthentication no
AllowGroups ssh-users
EOF
sudo systemctl restart sshd
```

> **Antes de cerrar la sesión actual**, abre otra ventana de PowerShell y comprueba que `ssh notas` entra. Si algo falló, corrígelo desde la primera ventana; así no te quedas fuera del servidor.

`AllowGroups` hace que solo entren por SSH las cuentas que agregues a `ssh-users`.

---

## 3. Usuarios y privilegios (servidor compartido)

Si otras personas usarán el servidor para pruebas, cada una tiene **su propio usuario y su propia llave**. Así cada acción queda registrada con su nombre, y quitarle el acceso a alguien no afecta al resto. La configuración es: **llave para entrar, contraseña personal solo para `sudo`**.

### Dar de alta a una persona

Pídele que genere su llave en su PC y te envíe la parte pública (la línea que empieza con `ssh-ed25519`). Nunca la privada.

```bash
sudo useradd -m ana
sudo passwd ana                          # contraseña personal, solo para sudo
sudo usermod -aG ssh-users ana
sudo mkdir -p /home/ana/.ssh
echo "ssh-ed25519 AAAA... ana@laptop" | sudo tee /home/ana/.ssh/authorized_keys
sudo chown -R ana:ana /home/ana/.ssh
sudo chmod 700 /home/ana/.ssh && sudo chmod 600 /home/ana/.ssh/authorized_keys
sudo restorecon -R /home/ana/.ssh
```

Para quitarle el acceso: `sudo usermod -L ana` (bloquea y conserva sus archivos) o `sudo userdel -r ana` (borra el usuario y su carpeta).

### Niveles de privilegio

| Nivel | Cómo | Para quién |
|---|---|---|
| Administrador | `sudo usermod -aG wheel usuario` | Solo tú |
| `sudo` para comandos concretos | archivo en `/etc/sudoers.d/` | Quien necesite una tarea puntual |
| Contenedores propios aislados | Podman sin privilegios (rootless) | Personas que hacen pruebas |
| Grupo `docker` | `sudo usermod -aG docker usuario` | Solo personas de total confianza |

**`sudo` limitado.** Edita siempre con `visudo`, que revisa la sintaxis antes de guardar (un error puede dejarte sin `sudo`):

```bash
sudo groupadd testers
sudo visudo -f /etc/sudoers.d/testers
```
```
%testers ALL=(root) /usr/bin/docker compose -f /opt/infra/docker-compose.yml restart caddy
```

Piensa bien cada comando que permites: algunos programas permiten abrir una consola de administrador desde dentro.

**Cuidado con el grupo `docker`: equivale a ser administrador del servidor.** Quien puede lanzar contenedores puede montar el disco completo dentro de uno y modificar cualquier archivo, sin `sudo`. Dalo solo a quien le darías `wheel`.

**Podman sin privilegios** (incluido en Rocky) es la alternativa para un espacio compartido: cada persona ejecuta contenedores con su propio usuario, aislados de los demás y sin poder tocar el sistema ni los proyectos de otros. Los `docker-compose.yml` suelen funcionar con `podman compose` con pocos cambios. Para que sus contenedores sigan corriendo después de cerrar la sesión SSH: `sudo loginctl enable-linger ana`.

### Alternativa para grupos: Tailscale SSH

Si el grupo usa Tailscale (sección 9), **Tailscale SSH** evita manejar llaves: cada persona entra con su cuenta de Tailscale y desde el panel web defines quién entra y con qué usuario de Linux. Los usuarios, grupos y `sudo` de Linux funcionan igual. Se activa con `sudo tailscale up --ssh`.

---

## 4. Docker

Rocky trae Podman; estos proyectos usan Docker:

```bash
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker frank           # tú (ver la advertencia de la sección 3)
```

Sal de la sesión SSH y vuelve a entrar para que el grupo surta efecto. Con `enable`, Docker arranca solo al encender el servidor, y los contenedores vuelven solos gracias a `restart: unless-stopped`.

```bash
sudo mkdir -p /opt/infra /opt/proyectos
sudo chown frank:frank /opt/infra /opt/proyectos
```

---

## 5. Firewall

```bash
sudo firewall-cmd --permanent --add-service=http --add-service=https --add-service=dns
sudo firewall-cmd --permanent --add-port=443/udp      # HTTP/3 (opcional)
sudo firewall-cmd --permanent --add-port=3000/tcp     # panel de AdGuard Home (configuración inicial)
sudo firewall-cmd --reload
```

Docker agrega sus propias reglas de red, por lo que los puertos que publica un contenedor suelen quedar accesibles aunque firewalld no los tenga abiertos. Ábrelos igual para que quede explícito, pero no uses firewalld para *bloquear* puertos de Docker: si un proyecto no debe ser accesible, no publiques su puerto.

Comprueba que el puerto 53 (DNS) esté libre antes de instalar AdGuard Home: `sudo ss -lunp | grep ':53 '` no debería mostrar nada.

---

## 6. Proxy inverso y DNS (repositorio servidor-casa)

Sigue el README del repositorio **servidor-casa**: se clona en `/opt/infra`, se levanta con `docker compose up -d`, se configura AdGuard Home y se apunta el DNS del router al servidor. Al terminar existe la red compartida `proxy` y `*.home.arpa` apunta al servidor.

Hazlo **antes** del paso 7: Notas en modo proxy necesita que la red `proxy` exista.

---

## 7. Instalar Notas

```bash
cd /opt/proyectos
git clone https://github.com/FrankVDev/Notas.git notas
cd notas
cp .env.example .env
nano .env
```

En `.env`:

- `POSTGRES_PASSWORD`: una contraseña larga y propia.
- `COMPOSE_FILE=docker-compose.yml:docker-compose.proxy.yml`: descomenta esta línea. La app deja de publicar el puerto 8080 y solo se llega a ella por Caddy.
- `ALLOW_SIGNUP`: déjalo en `true` hasta crear las cuentas (paso 10).

Si vas a **mudar tus notas** desde otra PC, sigue el paso 8 antes de levantar todo. Si empiezas de cero:

```bash
docker compose up -d --build
```

---

## 8. Mudar las notas desde tu PC con Windows

**En la PC con Windows**, desde la carpeta del proyecto:

```powershell
docker compose exec db pg_dump -U notas -d notas --no-owner -f /tmp/mudanza.sql
docker compose cp db:/tmp/mudanza.sql ./mudanza.sql
scp .\mudanza.sql notas:/opt/proyectos/notas/
```

**En el servidor**, con el `.env` ya configurado pero **sin haber levantado la API**:

```bash
cd /opt/proyectos/notas
docker compose up -d db
docker compose cp mudanza.sql db:/tmp/mudanza.sql
docker compose exec db psql -U notas -d notas -f /tmp/mudanza.sql -q
docker compose up -d --build
rm mudanza.sql
```

El orden importa: si la API arranca antes, crea tablas vacías y la restauración choca con ellas. Si te pasó, empieza de nuevo con `docker compose down -v` (borra la base del servidor, no la de tu PC) y repite.

Las cuentas, contraseñas y notas se mudan tal cual. En tus dispositivos tendrás que iniciar sesión otra vez porque la dirección cambia.

---

## 9. Acceso desde fuera de casa (opcional): Tailscale

Las VPN comerciales gratuitas no sirven para esto: sacan tu tráfico a internet, pero no conectan tus dispositivos con tu servidor. Tailscale sí, con un plan gratuito para uso personal y sin abrir puertos en el router:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Instala la app de Tailscale en tus dispositivos con la misma cuenta. Para que `notas.home.arpa` funcione también fuera de casa (y en Android aunque uses el DNS privado de Cloudflare), el servidor anuncia la red de casa por Tailscale ("subnet router") y se configura el split DNS. Los pasos están en la sección *Acceso desde fuera de casa* del README de servidor-casa.

El tráfico de Tailscale va cifrado de extremo a extremo, así que usar http dentro de la VPN no expone tu contraseña.

---

## 10. Verificar y cerrar el registro

```bash
cd /opt/proyectos/notas
docker compose ps                                     # db, api, web y backup en "running"
docker compose exec api alembic current               # 0003 (head)
docker compose exec api python -m app.admin usuarios  # tus cuentas, si mudaste las notas
ls respaldos/                                         # el primer respaldo automático
```

Abre `http://notas.home.arpa` desde otro dispositivo de la red. Cuando existan todas las cuentas necesarias, en `.env` pon `ALLOW_SIGNUP=false` y ejecuta `docker compose up -d`. Para crear cuentas después: `docker compose exec api python -m app.admin crear nombre`.

---

## 11. Mantenimiento

| Tarea | Comando |
|---|---|
| Actualizar Notas | `cd /opt/proyectos/notas && git pull && docker compose up -d --build` |
| Ver errores | `docker compose logs -f api` |
| Consumo de cada contenedor | `docker stats` |
| Espacio ocupado | `docker system df` |
| Limpiar imágenes viejas | `docker image prune` |
| Actualizar el sistema | `sudo dnf -y update` (y reiniciar si actualizó el kernel) |

Copia de vez en cuando la carpeta `respaldos/` fuera del servidor: si falla el disco, se pierden la base y los respaldos a la vez. Desde Windows: `scp -r notas:/opt/proyectos/notas/respaldos .`

---

## Problemas comunes

**El contenedor `backup` falla con "Permission denied".** SELinux está bloqueando la carpeta. Comprueba que en `docker-compose.yml` las líneas de `respaldos` y `backup.sh` terminan en `:z`, y revisa los bloqueos con `sudo ausearch -m avc -ts recent`.

**`network proxy declared as external, but could not be found`.** Falta levantar la infraestructura (paso 6) o se levantó con otro nombre de red.

**`notas.home.arpa` no abre en un dispositivo.** Prueba `http://notas.home.arpa/api/health`. Si el nombre no resuelve, el dispositivo no está usando AdGuard Home como DNS: revisa el router, y en Android, el DNS privado (Ajustes → Conexiones → DNS privado → Automático) o usa Tailscale.

**Caddy responde 502.** Caddy no llega a Notas: comprueba que `docker compose ps` muestre `web` en marcha y que `.env` tenga la línea `COMPOSE_FILE` activa.

**No puedes entrar por SSH después de cerrar las contraseñas.** Entra con pantalla y teclado, revisa `/etc/ssh/sshd_config.d/50-seguridad.conf` y que tu usuario esté en `ssh-users` (`id frank`).
