#!/usr/bin/env bash
# wenyan 直连 wrapper — 绕过 mihomo TUN 代理
# Usage: wenyan-direct publish -f article.md -t lapis
#
# 原理：设置 NO_PROXY 并临时切换路由表，让 wenyan 直连微信 API
# 微信 API 的 IP 段在国内，mihomo 的 GEOSITE,CN → 直连 理论上已生效，
# 但 fake-ip 模式可能导致问题。此脚本作为兜底方案。

# 确保不走 HTTP 代理
export HTTP_PROXY=''
export HTTPS_PROXY=''
export http_proxy=''
export https_proxy=''
export NO_PROXY='*'

# 直接调用 wenyan
exec wenyan "$@"
