#!/usr/bin/env bash
# wechat-publisher 环境配置（Hermes Linux 版）
# Usage: source ./scripts/env.sh
#
# 凭证优先级：
#   1. 环境变量 WECHAT_APP_ID / WECHAT_APP_SECRET（已设置则跳过）
#   2. wenyan credential 存储（~/.config/wenyan-md/credential.json）
#   3. 手动输入

if [ -n "$WECHAT_APP_ID" ] && [ -n "$WECHAT_APP_SECRET" ]; then
    echo "✅ 凭证已从环境变量加载"
    return 0
fi

# 尝试从 wenyan credential 读取
CRED_FILE="$HOME/.config/wenyan-md/credential.json"
if [ -f "$CRED_FILE" ]; then
    # credential.json 结构: {"wechat": {"wx_appid": {"appSecret": "...", "alias": "..."}}}
    WECHAT_APP_ID=$(python3 -c "
import json
d=json.load(open('$CRED_FILE'))
wechat=d.get('wechat',{})
print(list(wechat.keys())[0] if wechat else '')
" 2>/dev/null)
    WECHAT_APP_SECRET=$(python3 -c "
import json
d=json.load(open('$CRED_FILE'))
wechat=d.get('wechat',{})
appid=list(wechat.keys())[0] if wechat else ''
print(wechat[appid].get('appSecret','') if appid and appid in wechat else '')
" 2>/dev/null)
    if [ -n "$WECHAT_APP_ID" ] && [ -n "$WECHAT_APP_SECRET" ]; then
        export WECHAT_APP_ID WECHAT_APP_SECRET
        echo "✅ 凭证已从 wenyan credential 加载"
        echo "  AppID: ${WECHAT_APP_ID:0:10}..."
        return 0
    fi
fi

# 都失败则提示
echo "❌ 未找到微信公众号凭证"
echo ""
echo "请选择任一方式配置："
echo ""
echo "方式 1 — 交互式配置："
echo "  wenyan credential --set"
echo ""
echo "方式 2 — 环境变量："
echo "  export WECHAT_APP_ID=wx_your_app_id"
echo "  export WECHAT_APP_SECRET=your_app_secret"
echo ""
echo "方式 3 — 写入 ~/.bashrc 永久生效："
echo "  echo 'export WECHAT_APP_ID=wx_your_app_id' >> ~/.bashrc"
echo "  echo 'export WECHAT_APP_SECRET=your_app_secret' >> ~/.bashrc"
