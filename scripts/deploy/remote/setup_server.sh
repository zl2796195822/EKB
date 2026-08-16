#!/usr/bin/env bash
# EKB 服务器一次性初始化（裸金属部署，支持 Debian/Ubuntu + RHEL/CentOS/Rocky 双系列）。
# 幂等：重复执行不会破坏已有状态。

set -euo pipefail

log()  { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
ok()   { printf '[%s] ✅ %s\n' "$(date '+%H:%M:%S')" "$*"; }
warn() { printf '[%s] ⚠  %s\n' "$(date '+%H:%M:%S')" "$*" 1>&2; }

if [[ $(id -u) -ne 0 ]]; then
  echo "请以 root 身份运行：sudo $0" >&2; exit 1
fi

# === Distro 检测 ===
REL_ID="$(grep -oP '^ID=\K.*' /etc/os-release 2>/dev/null | tr -d '"' | tr '[:upper:]' '[:lower:]')"
REL_VER="$(grep -oP '^VERSION_ID=\K.*' /etc/os-release 2>/dev/null | tr -d '"' | cut -d. -f1)"

HAS_DNF=0; HAS_YUM=0; HAS_APT=0
command -v dnf   >/dev/null 2>&1 && HAS_DNF=1
command -v yum   >/dev/null 2>&1 && HAS_YUM=1
command -v apt   >/dev/null 2>&1 && HAS_APT=1
command -v apt-get >/dev/null 2>&1 && HAS_APT=1

PKG=""
if [[ ${HAS_DNF} -eq 1 ]]; then PKG="dnf"
elif [[ ${HAS_YUM} -eq 1 ]]; then PKG="yum"
elif [[ ${HAS_APT} -eq 1 ]]; then PKG="apt"
fi
log "检测发行版：${REL_ID:-unknown} ${REL_VER:-?} → 使用包管理器：${PKG:-none}"
if [[ -z "${PKG}" ]]; then
  warn "没找到可用包管理器（apt/dnf/yum 均不存在）。请先装好再跑本脚本。"; exit 1
fi

# === 1. 国内镜像加速（可选）===
if [[ "${PKG}" == "apt" ]]; then
  export DEBIAN_FRONTEND=noninteractive
  if [[ -f /etc/apt/sources.list ]] && ! grep -qE "aliyun|tsinghua|ustc" /etc/apt/sources.list 2>/dev/null; then
    log "切换 apt 源到阿里云镜像..."
    REL_CODENAME="$(grep -oP '^VERSION_CODENAME=\K.*' /etc/os-release 2>/dev/null | tr -d '"')"
    if [[ -n "${REL_CODENAME}" ]]; then
      cp -a /etc/apt/sources.list "/etc/apt/sources.list.bak.$(date +%s)" 2>/dev/null || true
      case "${REL_ID}" in
        ubuntu) cat >/etc/apt/sources.list <<EOF
deb http://mirrors.aliyun.com/ubuntu/ ${REL_CODENAME} main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu/ ${REL_CODENAME}-security main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu/ ${REL_CODENAME}-updates main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu/ ${REL_CODENAME}-backports main restricted universe multiverse
EOF
          ;;
        debian) cat >/etc/apt/sources.list <<EOF
deb http://mirrors.aliyun.com/debian/ ${REL_CODENAME} main contrib non-free non-free-firmware
deb http://mirrors.aliyun.com/debian-security ${REL_CODENAME}-security main contrib non-free non-free-firmware
EOF
          ;;
      esac
    fi
  fi
fi
if [[ "${PKG}" == "dnf" || "${PKG}" == "yum" ]]; then
  # CentOS 8 已经 official EOL，baseurl 切到 vault 或阿里云
  if [[ "${REL_ID}" == "centos" ]] && [[ "${REL_VER}" == "8" ]]; then
    if ! grep -qE "aliyun|vault.centos" /etc/yum.repos.d/*.repo 2>/dev/null; then
      log "CentOS 8 base repos → 阿里云 mirrors"
      for f in /etc/yum.repos.d/CentOS-*.repo; do
        [[ -f "$f" ]] || continue
        cp -a "$f" "$f.bak.$(date +%s)" 2>/dev/null || true
        sed -i \
          -e 's|^mirrorlist=|#mirrorlist=|g' \
          -e 's|^#baseurl=http://mirror.centos.org|baseurl=https://mirrors.aliyun.com|g' \
          -e 's|^baseurl=http://mirror.centos.org|baseurl=https://mirrors.aliyun.com|g' \
          "$f" || true
      done
      # CentOS 8 的 extras 常见不存在，先全部置 enabled=0 以避免 metadata 404；只保留 BaseOS/AppStream/PowerTools
      if [[ -d /etc/yum.repos.d ]]; then
        grep -rl "centos.org" /etc/yum.repos.d/ 2>/dev/null | xargs -I{} sed -i 's/^enabled=1/enabled=1/g' {} >/dev/null 2>&1 || true
      fi
    fi
  fi
fi

# === 2. 安装系统包 ===
log "安装依赖（nginx / python3 / 编译工具链 / curl）..."
case "${PKG}" in
  apt)
    apt-get update -y >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      nginx curl ca-certificates acl \
      python3 python3-venv python3-pip python3-dev \
      build-essential libssl-dev libffi-dev pkg-config >/dev/null 2>&1 || \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      nginx curl ca-certificates acl \
      python3 python3-pip \
      build-essential libssl-dev libffi-dev pkg-config
    ;;
  dnf|yum)
    # RHEL/CentOS: 需要 Development Tools + python38+ (CentOS 8 默认 python3.6 也行，python3.9 通过 powertools)
    ${PKG} install -y --setopt=tsflags=nodocs \
      nginx curl ca-certificates acl \
      python3 python3-devel python3-pip platform-python-setuptools \
      "@Development Tools" openssl-devel libffi-devel pkg-config policycoreutils >/dev/null 2>&1 || \
    ${PKG} install -y --setopt=tsflags=nodocs \
      nginx curl ca-certificates acl \
      python3 python36-devel python3-pip \
      gcc gcc-c++ make openssl-devel libffi-devel pkgconfig
    # CentOS 8: PowerTools/CRB 提供一些 devel 包（pip 编译需要）
    if command -v dnf config-manager >/dev/null 2>&1; then
      dnf config-manager --set-enabled powertools >/dev/null 2>&1 || \
      dnf config-manager --set-enabled crb >/dev/null 2>&1 || true
    fi
    # SELinux 友好：允许 nginx 连接本地 upstream（默认 enforcing 下 httpd_can_network_connect 是 off）
    if command -v setsebool >/dev/null 2>&1; then
      setsebool -P httpd_can_network_connect 1 >/dev/null 2>&1 || true
    fi
    ;;
esac
ok "系统依赖安装完成"

# === 3. 部署用户 & 目录结构 ===
if ! id ekb >/dev/null 2>&1; then
  useradd -r -s /usr/sbin/nologin -d /opt/ekb -m ekb 2>/dev/null || true
  ok "已创建运行用户 ekb"
else
  log "运行用户 ekb 已存在"
fi

mkdir -p /opt/ekb/api/releases /opt/ekb/api/venv /opt/ekb/web/releases /opt/ekb/data
mkdir -p /opt/ekb/api/releases/placeholder /opt/ekb/web/releases/placeholder  # 占位，软链可指向
ln -sf /opt/ekb/api/releases/placeholder /opt/ekb/api/current 2>/dev/null || true
ln -sf /opt/ekb/web/releases/placeholder /opt/ekb/web/current 2>/dev/null || true

chown -R ekb:ekb /opt/ekb
chmod 755 /opt/ekb
chmod 750 /opt/ekb/data
chmod 755 /opt/ekb/api /opt/ekb/web
ok "目录结构已初始化：/opt/ekb/{api,web,data}"

# === 4. Python venv + pip 清华源 ===
if [[ ! -x /opt/ekb/api/venv/bin/python3 ]]; then
  if ! python3 -m venv /opt/ekb/api/venv 2>/dev/null; then
    warn "python3 -m venv 失败（可能 ensurepip 不可用）；尝试 --without-pip + get-pip.py"
    python3 -m venv --without-pip /opt/ekb/api/venv 2>/dev/null || python3 -m venv --clear --without-pip /opt/ekb/api/venv
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    /opt/ekb/api/venv/bin/python3 /tmp/get-pip.py --quiet || {
      # 如果 get-pip 也不行，用系统 pip --target 安装
      warn "get-pip 也失败；尝试使用系统 pip 安装 setuptools+wheel 进 venv"
      python3 -m pip install --target /opt/ekb/api/venv/lib/python*/site-packages --quiet pip setuptools wheel 2>/dev/null || true
    }
    rm -f /tmp/get-pip.py
  fi
fi
VENV_PIP=/opt/ekb/api/venv/bin/pip
if [[ -x "${VENV_PIP}" ]]; then
  "${VENV_PIP}" install --quiet --upgrade pip setuptools wheel >/dev/null 2>&1 || true
  "${VENV_PIP}" config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >/dev/null 2>&1 || true
  "${VENV_PIP}" config set install.trusted-host pypi.tuna.tsinghua.edu.cn >/dev/null 2>&1 || true
  ok "venv 已就绪，pip 源 = 清华"
else
  warn "venv 中没有 pip；将在 deploy.sh 首轮部署时再尝试补齐。"
fi

# === 5. nginx vhost（distro-aware：conf.d 或 sites-enabled）===
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NGINX_TMPL="${SCRIPT_DIR}/nginx_ekb.conf.tmpl"
if [[ -f "${NGINX_TMPL}" ]]; then
  NGINX_DEST=""
  if [[ -d /etc/nginx/conf.d ]]; then
    NGINX_DEST="/etc/nginx/conf.d/ekb.conf"
    cp "${NGINX_TMPL}" "${NGINX_DEST}"
    # CentOS 默认 /etc/nginx/nginx.conf 已 include /etc/nginx/conf.d/*.conf；且 default_server 可能在 conf.d/default.conf 或 nginx.conf
    # 我们要让 ekb 当 default_server：tmpl 里写了 default_server；冲突则先把旧 default.conf 改名
    if [[ -f /etc/nginx/conf.d/default.conf ]]; then
      if grep -q "listen.*80.*default_server" /etc/nginx/conf.d/default.conf 2>/dev/null; then
        mv /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf.disabled 2>/dev/null || true
      fi
    fi
    if [[ -f /etc/nginx/nginx.conf ]]; then
      # nginx.conf 里可能 server { listen 80 default_server } 要注释掉
      if grep -q "listen.*80.*default_server" /etc/nginx/nginx.conf 2>/dev/null; then
        cp -a /etc/nginx/nginx.conf /etc/nginx/nginx.conf.bak."$(date +%s)"
        sed -i -E 's/listen\s+80\s+default_server/listen      8081/' /etc/nginx/nginx.conf 2>/dev/null || true
        # 如果 sed 把 server block 整个弄乱就回滚：再做 nginx -t 校验，失败就回滚 nginx.conf
        if ! nginx -t >/dev/null 2>&1; then
          mv /etc/nginx/nginx.conf.bak.* /etc/nginx/nginx.conf 2>/dev/null || true
        fi
      fi
    fi
  elif [[ -d /etc/nginx/sites-available ]]; then
    mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
    NGINX_DEST="/etc/nginx/sites-available/ekb.conf"
    cp "${NGINX_TMPL}" "${NGINX_DEST}"
    [[ -f /etc/nginx/sites-enabled/default ]] && rm -f /etc/nginx/sites-enabled/default
    ln -sf /etc/nginx/sites-available/ekb.conf /etc/nginx/sites-enabled/ekb.conf
  else
    warn "nginx 配置目录既无 conf.d 也无 sites-available；手动写入 /etc/nginx/ekb.conf 并在 nginx.conf include"
    mkdir -p /etc/nginx
    NGINX_DEST="/etc/nginx/ekb.conf"
    cp "${NGINX_TMPL}" "${NGINX_DEST}"
    if ! grep -q "include.*ekb.conf" /etc/nginx/nginx.conf 2>/dev/null; then
      cp -a /etc/nginx/nginx.conf /etc/nginx/nginx.conf.bak."$(date +%s)"
      # 在 http block 的最后一个 include 之后或 } 之前 include ekb.conf
      sed -i -E '/^\s*http\s*\{/a\    include \/etc\/nginx\/ekb.conf;' /etc/nginx/nginx.conf 2>/dev/null || true
    fi
  fi
  log "nginx 配置写入：${NGINX_DEST}"
  # === 5b. 可选 TLS vhost：证书就位才启用，追加到同一配置文件（随 nginx -t 一起校验）===
  TLS_TMPL="${SCRIPT_DIR}/nginx_ekb_tls.conf.tmpl"
  if [[ -f "${TLS_TMPL}" ]]; then
    if [[ -f /etc/nginx/ekb-certs/fullchain.pem && -f /etc/nginx/ekb-certs/privkey.pem ]]; then
      cat "${TLS_TMPL}" >> "${NGINX_DEST}"
      chmod 600 /etc/nginx/ekb-certs/privkey.pem 2>/dev/null || true
      log "TLS vhost 已追加：${NGINX_DEST}（检测到 /etc/nginx/ekb-certs 证书）"
    else
      log "未检测到 /etc/nginx/ekb-certs/{fullchain,privkey}.pem；跳过 TLS vhost（仅明文 80）"
    fi
  fi
  if nginx -t >/dev/null 2>&1; then
    systemctl enable --now nginx >/dev/null 2>&1 || systemctl enable nginx >/dev/null 2>&1 || true
    systemctl reload nginx 2>/dev/null || systemctl restart nginx || true
    ok "nginx reload 通过"
  else
    warn "nginx -t 校验失败（见下方输出）"
    nginx -t 1>&2 || true
  fi
else
  warn "未找到 nginx_ekb.conf.tmpl（路径 ${NGINX_TMPL}）"
fi

# === 6. systemd unit ===
UNIT_TMPL="${SCRIPT_DIR}/ekb-api.service.tmpl"
if [[ -f "${UNIT_TMPL}" ]]; then
  cp "${UNIT_TMPL}" /etc/systemd/system/ekb-api.service
  systemctl daemon-reload
  systemctl disable ekb-api.service >/dev/null 2>&1 || true
  ok "systemd unit 已写入 /etc/systemd/system/ekb-api.service（暂不启动）"
fi

# === 7. 默认 .env ===
ENV_TMPL="${SCRIPT_DIR}/server.env.tmpl"
if [[ -f "${ENV_TMPL}" ]] && [[ ! -f /opt/ekb/api/.env ]]; then
  cp "${ENV_TMPL}" /opt/ekb/api/.env
  chown ekb:ekb /opt/ekb/api/.env
  chmod 640 /opt/ekb/api/.env
  ok ".env 模板写入 /opt/ekb/api/.env"
fi

# === 8. 防火墙放 80 ===
if command -v firewall-cmd >/dev/null 2>&1; then
  firewall-cmd --add-service=http --permanent >/dev/null 2>&1 || firewall-cmd --add-port=80/tcp --permanent >/dev/null 2>&1 || true
  firewall-cmd --reload >/dev/null 2>&1 || true
  ok "firewalld：放行 80/tcp"
fi
if command -v ufw >/dev/null 2>&1; then
  ufw allow 80/tcp >/dev/null 2>&1 || true
  ok "ufw：放行 80/tcp"
fi
# iptables 兜底：如果以上都没生效，临时加 INPUT ACCEPT（重启失效，但可用于验证）
if command -v iptables >/dev/null 2>&1; then
  iptables -C INPUT -p tcp --dport 80 -j ACCEPT >/dev/null 2>&1 || \
  iptables -I INPUT -p tcp --dport 80 -j ACCEPT >/dev/null 2>&1 || true
fi

# === 9. 数据目录权限 ===
chown -R ekb:ekb /opt/ekb/data
chmod 700 /opt/ekb/data

echo
echo "=== 服务器初始化完成（CentOS/Debian 自适应）==="
echo "  发行版: ${REL_ID:-unknown} ${REL_VER:-?}"
echo "  包管理器: ${PKG}"
echo "  部署用户: ekb"
echo "  部署目录: /opt/ekb/api, /opt/ekb/web, /opt/ekb/data (SQLite)"
echo "  服务: nginx (:80) · ekb-api.service (127.0.0.1:8000, 待 deploy.sh 启动)"
echo "  下一步: 在受管运行环境配置运行时部署目标与凭据后执行 deploy.sh --skip-tests"
