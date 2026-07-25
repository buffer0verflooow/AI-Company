# AI Agent / LLM Security — Curated Reading List (2026)

> Curated from top security vendors, research labs, academic sources, and Chinese tech media.
> Focus: technical depth over press/news. Each entry rated for technical depth (1–5) and relevance to AI agent security.

---

## 1. VentureBeat — Deep Dives

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 1.1 | An AI agent rewrote a Fortune 50 security policy. Here's the identity model that let it pass audit | [link](https://venturebeat.com/security/cisco-crowdstrike-rsac-2026-agent-identity-iam-gap-maturity-model) | 4 | High | 2026-06 | Cisco's 6-stage identity maturity model for AI agents, applied to a Fortune 50 rewriting its own security policy |
| 1.2 | The agent security gap: 54% of enterprises have already had an AI agent incident | [link](https://venturebeat.com/ai/the-agent-security-gap-54-of-enterprises-have-already-had-an-ai-agent-incident-and-most-still-let-agents-share-credentials) | 3 | High | 2026 | Survey of 107 enterprises reveals credential-sharing and access-control gaps behind most agent incidents |
| 1.3 | 85% of enterprises are running AI agents. Only 5% trust them enough to ship | [link](https://venturebeat.com/security/85-of-enterprises-are-running-ai-agents-only-5-trust-them-enough-to-ship) | 3 | High | 2026 | Cisco AI Defense now 100% AI-built; zero human-written code; trust gap analysis across enterprises |
| 1.4 | RSAC 2026 shipped five agent identity frameworks — and left three gaps | [link](https://venturebeat.com/security/rsac-2026-agent-identity-frameworks-three-gaps) | 5 | High | 2026-05 | CrowdStrike, Cisco, Palo Alto, Microsoft & Cato CTRL agent identity frameworks compared; needle-move, trust-transport, and audit-chaining gaps identified |
| 1.5 | AI agent credentials live in the same box as untrusted code — Anthropic and Nvidia ship zero-trust fixes | [link](https://venturebeat.com/security/ai-agent-zero-trust-architecture-audit-credential-isolation-anthropic-nvidia-nemoclaw) | 5 | High | 2026 | Anthropic's credential isolation vs Nvidia Nemoclaw approach to zero-trust AI agent architectures; Gravitee report cited |
| 1.6 | Meta's rogue AI agent passed every identity check | [link](https://venturebeat.com/security/meta-rogue-ai-agent-confused-deputy-iam-identity-governance-matrix) | 4 | High | 2026 | Confused-deputy attack on Meta's agent; IAM governance matrix fails at runtime; Saviynt CISO Risk Report |
| 1.7 | Nvidia's agentic AI stack is the first major platform to ship five-vendor governance framework | [link](https://venturebeat.com/security/nvidia-gtc-2026-agentic-ai-security-five-vendor-governance-framework) | 4 | High | 2026-03 | Nvidia GTC 2026: agentic AI security stack with multi-vendor governance; 48% of pros rank agentic AI as top attack vector |

---

## 2. Cloudflare Blog — Agents Week & Beyond

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 2.1 | Everything we launched during Agents Week 2026 | [link](https://blog.cloudflare.com/agents-week-in-review/) | 4 | High | 2026 | Outbound Workers for Sandboxes: programmable zero-trust egress proxy for AI agents; credential injection architecture |
| 2.2 | Welcome to Agents Week | [link](https://blog.cloudflare.com/welcome-to-agents-week/) | 3 | High | 2026 | Overview of Cloudflare's agent stack across compute, connectivity, security, identity, and economics |
| 2.3 | Temporary Cloudflare Accounts for AI agents | [link](https://blog.cloudflare.com/temporary-accounts/) | 4 | Medium | 2026 | Ephemeral identity provisioning for agents; deploy agents without pre-provisioned credentials |
| 2.4 | How we built Cloudflare's data platform and an AI agent on top of it | [link](https://blog.cloudflare.com/our-unified-data-platform/) | 5 | Medium | 2026 | Town Lake platform architecture + Skipper AI agent; practical agent-on-platform case study |
| 2.5 | Bringing more agent harnesses and frameworks to Cloudflare's platform | [link](https://blog.cloudflare.com/agents-platform-flue-sdk/) | 4 | High | 2026 | Agent harness architecture (Codex, Claude Code, etc.); model access control patterns |
| 2.6 | Your site, your rules: new AI traffic options for all customers | [link](https://blog.cloudflare.com/content-independence-day-ai-options/) | 3 | Medium | 2026-09 | AI traffic classification (Training vs Agent vs Search); default blocking by category |

---

## 3. Wiz Blog & Academy

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 3.1 | Securing Agentic AI: What Cloud Teams Need To Know | [link](https://www.wiz.io/academy/ai-security/securing-agentic-ai) | 4 | High | 2026 | Wiz Academy: agentic AI security covering autonomous decision-making, tool use, and human-approval gaps |
| 3.2 | AI Agent Security: 6 Risks to Address and How to Do It | [link](https://www.wiz.io/academy/ai-security/ai-agent-security) | 4 | High | 2026 | Six risk categories for autonomous AI agents; practical cloud security controls for each |
| 3.3 | AI Agents vs Humans: Who Wins at Web Hacking in 2026? | [link](https://www.wiz.io/blog/ai-agents-vs-humans-who-wins-at-web-hacking-in-2026) | 5 | High | 2026 | AI agents solved 9/10 web hacking challenges including multi-step exploits; offensive agent capability benchmark |
| 3.4 | State of AI in the Cloud 2026 | [link](https://www.wiz.io/reports/state-of-ai-in-the-cloud-2026) | 3 | Medium | 2026 | Industry report: self-hosted models, AI-generated code scaling systemic risks |
| 3.5 | AI Security Solutions in 2026: Tools To Secure AI | [link](https://www.wiz.io/academy/ai-security/ai-security-solutions) | 4 | Medium | 2026 | Tooling landscape: infrastructure security, data governance, agent permission restrictions, runtime monitoring |

---

## 4. Datadog Security Labs

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 4.1 | Entra Agent ID: The blueprint blast radius | [link](https://securitylabs.datadoghq.com/articles/agent-id-blueprint-blast-radius/) | 5 | High | 2026 | Deep technical analysis of Microsoft Entra Agent ID framework; blast radius implications for agent identity |
| 4.2 | Malicious Coding Agent Skills and the Risk of Dynamic Context | [link](https://securitylabs.datadoghq.com/articles/malicious-skills-supply-chain-risks-in-coding-agents-with-dynamic-context/) | 5 | High | 2026 | Supply chain attacks on coding agents via malicious skills; prompt injection + backdoor attacks mapped to OWASP Top 10 LLM |
| 4.3 | MCP vulnerability case study: SQL injection in the Postgres MCP server | [link](https://securitylabs.datadoghq.com/articles/mcp-vulnerability-case-study-SQL-injection-in-the-postgresql-mcp-server/) | 5 | High | 2026 | Security audit of open-source MCP servers; SQL injection in Postgres MCP — agentic AI tool security research |
| 4.4 | Introducing IDE-SHEPHERD: Your shield against threat actors | [link](https://securitylabs.datadoghq.com/articles/ide-shepherd-release-article/) | 4 | Medium | 2026 | Open-source IDE security extension for VS Code and Cursor; real-time monitoring against AI agent supply chain threats |
| 4.5 | LiteLLM and Telnyx compromised on PyPI (TeamPCP campaign) | [link](https://securitylabs.datadoghq.com/articles/litellm-compromised-pypi-teampcp-supply-chain-campaign/) | 4 | High | 2026-03 | Supply chain attack on AI agent infrastructure libraries; TeamPCP campaign analysis |
| 4.6 | Shai-Hulud Goes Open Source | [link](https://securitylabs.datadoghq.com/articles/shai-hulud-open-source-framework-static-analysis/) | 4 | Medium | 2026 | Open-source static analysis framework for detecting malicious npm/PyPI packages targeting AI agent supply chains |

---

## 5. Palo Alto Networks — Unit 42

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 5.1 | AI Agents Are Here. So Are the Threats. | [link](https://unit42.paloaltonetworks.com/agentic-ai-threats/) | 4 | High | 2026 | Agent credential theft, tool abuse, and unexpected behavior taxonomy from Unit 42 threat research |
| 5.2 | Web-Based Indirect Prompt Injection Observed in the Wild | [link](https://unit42.paloaltonetworks.com/ai-agent-prompt-injection/) | 5 | High | 2026 | Real-world indirect prompt injection cases in LLM-integrated web systems; attack chain analysis |
| 5.3 | Trust No Skill: Integrity Verification for AI Agent Supply Chains | [link](https://unit42.paloaltonetworks.com/ai-agent-supply-chain-risks/) | 5 | High | 2026 | Enterprise AI agent skill auditing; third-party skill vulnerability detection; multi-stage attack chain verification |
| 5.4 | Navigating Security Tradeoffs of AI Agents | [link](https://unit42.paloaltonetworks.com/navigating-security-tradeoffs-ai-agents/) | 4 | High | 2026 | Security-vs-productivity tradeoff framework for agentic AI deployments |
| 5.5 | Can AI Attack the Cloud? Lessons From Building an Autonomous AI Attacker | [link](https://unit42.paloaltonetworks.com/autonomous-ai-cloud-attacks/) | 5 | High | 2026 | Multi-agent AI system autonomously attacking cloud environments; practical offensive agent research |
| 5.6 | Exposing Security Blind Spots in GCP Vertex AI (Double Agents) | [link](https://unit42.paloaltonetworks.com/double-agents-vertex-ai/) | 5 | High | 2026 | "Double agent" flaw in Google Cloud Vertex AI; overprivileged agents compromising cloud environments |
| 5.7 | Fracturing Software Security With Frontier AI Models | [link](https://unit42.paloaltonetworks.com/ai-software-security-risks/) | 4 | Medium | 2026 | Frontier AI models increasing zero-day and N-day vulnerability risk; software security impact assessment |

---

## 6. CrowdStrike Blog

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 6.1 | How Agentic Tool Chain Attacks Threaten AI Agent Security | [link](https://www.crowdstrike.com/en-us/blog/how-agentic-tool-chain-attacks-threaten-ai-agent-security/) | 5 | High | 2026 | Three critical agentic tool chain attack classes; exploitation patterns and mitigations |
| 6.2 | The Identity Problem Hiding in AI Agent Deployments | [link](https://www.crowdstrike.com/en-us/blog/the-identity-problem-hiding-in-ai-agent-deployments/) | 4 | High | 2026 | Absence of standard identity context for agents; risk analysis per new agent deployment |
| 6.3 | 3 Principles to Safely Scale Agentic AI | [link](https://www.crowdstrike.com/en-us/blog/three-principles-to-safely-scale-agentic-ai/) | 4 | High | 2026 | Build-in vs bolt-on security; principles for agentic AI safety at scale |
| 6.4 | CrowdStrike Announces Continuous Identity for AI Agents | [link](https://www.crowdstrike.com/en-us/blog/crowdstrike-announces-continuous-identity-for-ai-agents/) | 3 | High | 2026 | CI for AI agents: risk-aware authorization across human, non-human, and AI agent identities |
| 6.5 | Practical 90-Day Roadmap for AI Agent Security (eBook) | [link](https://www.crowdstrike.com/en-us/resources/white-papers/ai-agent-security-architecture-attack-surface-defense/) | 4 | High | 2026 | Actionable 90-day roadmap: attack surface defense, architecture hardening for enterprise AI agents |
| 6.6 | Why AI Governance Without Guardrails Is Theater | [link](https://www.crowdstrike.com/en-us/blog/why-ai-governance-without-guardrails-is-theater/) | 3 | Medium | 2026 | Critique of AI governance approaches lacking runtime enforcement; what real guardrails require |

---

## 7. OWASP — GenAI & Agentic Security

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 7.1 | OWASP Top 10 for Agentic Applications 2026 | [link](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) | 5 | High | 2026-03 | **The** reference framework: ASI01-ASI10 including Agent Goal Hijack, Tool Misuse, Identity Abuse, Supply Chain, Code Execution |
| 7.2 | OWASP Agentic Security Initiative | [link](https://genai.owasp.org/initiatives/agentic-security-initiative/) | 4 | High | 2026 | OWASP's Agentic Security Initiative (ASI) — working group, threat library, and mitigation catalog |
| 7.3 | OWASP Top 10 for Agents 2026 — Detailed Breakdown (DeepTeam) | [link](https://trydeepteam.com/docs/frameworks-owasp-top-10-for-agentic-applications) | 5 | High | 2026 | Full ASI risk descriptions: Goal Hijack, Tool Misuse, Identity Abuse (ASI01-ASI04), with exploitation scenarios |
| 7.4 | Lessons from OWASP Top 10 for Agentic Applications (Auth0) | [link](https://auth0.com/blog/owasp-top-10-agentic-applications-lessons/) | 3 | Medium | 2026 | Identity-centric interpretation of ASI Top 10; Auth0 perspective on agent authentication |
| 7.5 | OWASP Top 10 Agentic AI Risks Explained (Human Security) | [link](https://www.humansecurity.com/learn/blog/owasp-top-10-agentic-applications/) | 4 | Medium | 2026 | Operational risk breakdown: AI assistants, LLM crawlers, automated browsers in scope |
| 7.6 | OWASP Top 10 Agents & AI Vulnerabilities (2026 Cheat Sheet) | [link](https://blog.alexewerlof.com/p/owasp-top-10-ai-llm-agents) | 4 | High | 2026 | Pragmatic engineering cheat sheet combining OWASP Top 10 LLM + Agent vulnerabilities in one reference |

---

## 8. Cloud Security Alliance (CSA) & NIST — Standards & Frameworks

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 8.1 | NIST AI Agent Standards Initiative | [link](https://www.nist.gov/artificial-intelligence/ai-agent-standards-initiative) | 5 | High | 2026-02 | NIST CAISI official launch; AI agent identity/authorization using OAuth 2.0, SPIFFE/SPIRE |
| 8.2 | CSA Research Note: NIST AI Agent Security — Red-Teaming Guidance | [link](https://labs.cloudsecurityalliance.org/research/csa-research-note-nist-ai-agent-red-teaming-standards-202603/) | 4 | High | 2026-03 | NCCoE concept paper: AI agent red-teaming standards, OAuth 2.0 identity demos |
| 8.3 | CSA Research Note: Federal Agentic AI Security — NIST's Emerging Standards Framework | [link](https://labs.cloudsecurityalliance.org/research/csa-research-note-nist-ai-agent-standards-federal-framework/) | 5 | High | 2026 | NIST AASI deep-dive: CAISI framework, federal compliance implications |
| 8.4 | NIST RFI: Security Considerations for AI Agents | [link](https://www.federalregister.gov/documents/2026/01/08/2026-00206/request-for-information-regarding-security-considerations-for-artificial-intelligence-agents) | 4 | High | 2026-01 | Formal NIST RFI on AI agent security; basis for regulatory framework |
| 8.5 | MCP by Design: RCE Across the AI Agent Ecosystem (CSA) | [link](https://labs.cloudsecurityalliance.org/research/csa-research-note-mcp-by-design-rce-ox-security-20260420-csa/) | 5 | High | 2026-04 | Systemic RCE vulnerability in Anthropic's MCP protocol; "mother of all AI supply chains" disclosure |

---

## 9. Gravitee — State of AI Agent Security Reports

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 9.1 | State of AI Agent Security 2026 Report | [link](https://www.gravitee.io/state-of-ai-agent-security) | 4 | High | 2026 | 919-enterprise survey: 88% with confirmed/suspected incidents; 81% past planning phase but only 14.4% fully approved |
| 9.2 | When Adoption Outpaces Control (Gravitee Blog) | [link](https://www.gravitee.io/blog/state-of-ai-agent-security-2026-report-when-adoption-outpaces-control) | 3 | High | 2026 | Key findings summary: 52% of agents running with zero security logs; adoption vs control gap |
| 9.3 | 88% of Companies Have Already Seen AI Agent Security Failures | [link](https://www.gravitee.io/blog/88-of-companies-have-already-seen-ai-agent-security-failures) | 3 | Medium | 2026 | Incident prevalence data from the 2026 report |

---

## 10. Academic Papers — arXiv & Peer-Reviewed

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 10.1 | Parallax: Why AI Agents That Think Must Never Act | [link](https://arxiv.org/html/2604.12986v1) | 5 | High | 2026 | 40% of AI agent frameworks contain exploitable prompt injection flaws in tool execution; separation of reasoning and action |
| 10.2 | The Attack and Defense Landscape of Agentic AI | [link](https://arxiv.org/html/2603.11088v1) | 5 | High | 2026 | First systematic survey of AI agent security; design space, attack taxonomy, defense strategies |
| 10.3 | A Systematic Survey of Security Threats and Defenses in AI Agents | [link](https://arxiv.org/html/2604.23338v2) | 5 | High | 2026 | Comprehensive threat taxonomy + defense mapping for LLM-powered agent ecosystems |
| 10.4 | A Security Analysis of the OpenClaw AI Agent Framework | [link](https://arxiv.org/abs/2603.27517) | 5 | High | 2026 | 470-advisory taxonomy of OpenClaw vulnerabilities; architectural layer security analysis |
| 10.5 | Execute-Only Agents: Architectural Defense Against Prompt Injection | [link](https://people.cs.vt.edu/djwillia/papers/agenticos26-xoa.pdf) | 5 | High | 2026 | Embedding-based classifiers for prompt injection detection in agent execution architectures |
| 10.6 | Caging the Agents: A Zero Trust Security Architecture for AI Agents | [link](https://arxiv.org/pdf/2603.17419) | 5 | High | 2026 | Zero-trust architecture formalization for AI agents; NIST AI Agent Standards Initiative alignment |
| 10.7 | Agentic AI Security: Threats, Defenses, Evaluation, and Survey (IEEE) | [link](https://ieeexplore.ieee.org/iel8/6287639/11323511/11447227.pdf) | 5 | High | 2026 | Comprehensive IEEE survey: agentic AI threat landscape, evaluation benchmarks, defense mechanisms |
| 10.8 | AEGIS: Security Sandboxing Meets Mechanistic Interpretability | [link](https://sspcdn.blob.core.windows.net/files/Documents/SEP/STS/2026/posters/2026STS_Lu.Kevin_poster.pdf) | 4 | High | 2026 | Sandboxing + mechanistic interpretability for AI agent runtime; indirect prompt injection mitigation |

---

## 11. Chinese Sources — 知乎专栏

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 11.1 | AI Agent 安全治理的分水岭已到来 | [link](https://zhuanlan.zhihu.com/p/2049909354250473753) | 5 | High | 2026-05 | Anthropic《Zero Trust for AI Agents》白皮书深度解读；业界最系统的Agent安全实施框架分析 |
| 11.2 | 深度解析 OWASP Agentic Top 10: AI Agent 的风险从模型转向Skill | [link](https://zhuanlan.zhihu.com/p/2030379522235834642) | 5 | High | 2026-04 | OWASP Agentic Top 10 中文深度解读；Agent 风险重心从模型层转移到 Skill/工具层 |
| 11.3 | 2026年自主智能体（AI Agent）前沿治理方案与安全对齐研究 | [link](https://zhuanlan.zhihu.com/p/2022575249175134537) | 5 | High | 2026 | 自主智能体新型风险结构；算法对齐、系统工程架构、全球监管合规、企业级安全运营四维分析 |
| 11.4 | 绿盟科技：解读AI Agent的网络攻防核心行动主体 | [link](https://zhuanlan.zhihu.com/p/2050264605528700535) | 4 | High | 2026 | 绿盟科技学术大会演讲：AI Agent从安全辅助工具蜕变为网络攻防核心行动主体 |
| 11.5 | RSAC 2026聚焦AI Agent安全，企业如何补齐AI治理短板？ | [link](https://zhuanlan.zhihu.com/p/2027075760968482843) | 4 | High | 2026 | RSAC 2026 AI Agent安全主题总结；Cisco零信任扩展至AI代理、"行动控制"(Action Control)框架 |
| 11.6 | 2026 AI安全十大预测 | [link](https://zhuanlan.zhihu.com/p/1996616935245382928) | 3 | Medium | 2026 | Agentic AI引发首起重大"自主式"运营事故预测；系统性漏洞风险分析 |
| 11.7 | 2026年Agentic AI十大关键趋势：技术、应用与治理三位一体 | [link](https://zhuanlan.zhihu.com/p/1991451643544355292) | 3 | Medium | 2026 | Agent安全风险新特征：传统防护无法应对自主威胁、Shadow AI系统风险 |
| 11.8 | 2026年的AI安全运营中心：下一代SOC平台的分水岭 | [link](https://zhuanlan.zhihu.com/p/1969084288571019768) | 4 | Medium | 2026 | AI驱动智能体体系重新定义SOC检测、响应与决策模式 |

---

## 12. Chinese Sources — 安全客 (Anquanke)

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 12.1 | 2026网络安全六大新趋势：AI重构攻防，信任成为新防线 | [link](https://www.anquanke.com/post/id/314019) | 4 | High | 2026 | AI Agent身份安全漏洞被无限放大；三种核心风险类型深度分析 |
| 12.2 | GPT-RED — AI网络安全进入「自我博弈」时代 | [link](https://www.anquanke.com/post/id/315811) | 5 | High | 2026-07 | OpenAI GPT-RED发布；复旦AgentCyberRange真实网络靶场；英国AISI安全评估里程碑 |
| 12.3 | 让OpenClaw安全上岗，火山引擎发布首个AI助手安全方案 | [link](https://www.anquanke.com/post/id/314871) | 4 | High | 2026 | 火山引擎AI助手安全方案：提示词注入防护、高危操作管控、敏感信息泄露防范、供应链攻击防御 |
| 12.4 | 重大安全信号藏在这场国际会议的"行动指南"里—RSAC 2026 | [link](https://www.anquanke.com/post/id/315481) | 4 | High | 2026 | 思科零信任扩展至AI代理；Action Control行动控制框架详细解读 |
| 12.5 | MS-Agent存在未修复漏洞（CVE-2026-2256），可劫持AI智能体 | [link](https://www.anquanke.com/post/id/314985) | 5 | High | 2026 | CVE-2026-2256详细分析：攻击者通过构造文本劫持AI智能体，控制底层计算机系统 |
| 12.6 | 2025，AI Agent时代的主动防御：安全防御体系的重构之年 | [link](https://www.anquanke.com/post/id/314865) | 4 | High | 2026 | Deepfake+数字人Agent冲击身份可信根基；凭证填充、MFA绕过、数字身份劫持攻击链 |

---

## 13. Chinese Sources — FreeBuf

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 13.1 | AI Agent扩大攻击面，大国博弈引发安全新挑战 (FreeBuf) | [link](https://m.freebuf.com/articles/469086.html) | 3 | Medium | 2026-01 | AI Agent攻击面扩大分析；Metasploit新模块与Agent在WebShell免杀比赛中的应用（搜索结果较有限） |

---

## 14. MCP Security & Supply Chain

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 14.1 | The State of MCP Security in 2026 | [link](https://nimblebrain.ai/mcp/mcp-security/state-of-mcp-security/) | 5 | High | 2026 | MCP protocol threat landscape; 42,000+ OpenClaw agent instances; supply chain attack vectors |
| 14.2 | MCP by Design: RCE Across the AI Agent Ecosystem | [link](https://labs.cloudsecurityalliance.org/research/csa-research-note-mcp-by-design-rce-ox-security-20260420-csa/) | 5 | High | 2026-04 | Systemic RCE in MCP protocol design; Ox Security disclosure |
| 14.3 | Your AI Agent Has a Supply Chain Problem (KuppingerCole) | [link](https://www.kuppingercole.com/blog/balaganski/your-ai-agent-has-a-supply-chain-problem) | 4 | High | 2026 | MCP as API security problem; agentic AI supply chain & runtime governance challenge |
| 14.4 | The 2026 Guide to Software Supply Chain Security (Cloudsmith) | [link](https://cloudsmith.com/blog/the-2026-guide-to-software-supply-chain-security-from-static-sboms-to-agentic-governance) | 4 | Medium | 2026 | MCP as emerging standard for agent communication; static SBOM to agentic governance evolution |
| 14.5 | MCP Security for Enterprises: Best Practices Checklist | [link](https://www.mintmcp.com/blog/mcp-security-enterprises) | 4 | Medium | 2026 | Access management, encryption, auditing, threat detection for MCP-based agent systems |

---

## 15. AI Agent Observability & Runtime Protection

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 15.1 | Best AI Agent Runtime Tools & Platforms in 2026 (Orca Security) | [link](https://orca.security/resources/blog/the-best-ai-agent-runtime-tools-platforms-in-2026/) | 3 | Medium | 2026 | Comparison of hyperscaler, framework-native, and sandbox agent runtimes |
| 15.2 | Top 7 AI Runtime Security Platforms for 2026 (Straiker) | [link](https://www.straiker.ai/blog/top-7-ai-runtime-security-platforms) | 4 | High | 2026 | Four-layer agent attack surface coverage: application, model, tool/MCP, data |
| 15.3 | Splunk Observability Q1 2026: Deeper Insights for AI Agents | [link](https://www.splunk.com/en_us/blog/observability/splunk-observability-ai-agent-monitoring-innovations.html) | 3 | Medium | 2026 | AI agent and infrastructure monitoring; performance, quality, cost, security risk tracking |
| 15.4 | The AI Security Stack of 2026 (Deepak Gupta) | [link](https://guptadeepak.com/research/ai-security-stack-2026/) | 5 | High | 2026 | Five-layer AI security stack: Governance, Red Teaming, MLSecOps, Threat Detection (LLM runtime), Agentic Defense |
| 15.5 | What Is an Agent Execution Sandbox? (Augment Code) | [link](https://www.augmentcode.com/guides/agent-execution-sandbox) | 4 | High | 2026 | Production isolation boundaries for AI-generated code; filesystem, network egress, credential restriction |
| 15.6 | Top 5 AI Agent Observability Platforms in 2026 | [link](https://www.getmaxim.ai/articles/top-5-ai-agent-observability-platforms-in-2026/) | 3 | Medium | 2026 | Platform capability comparison: performance monitoring, cost tracking, security observability |

---

## 16. AI Agent Red Teaming & Penetration Testing

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 16.1 | The 2026 Ultimate Guide to AI Penetration Testing: Agentic Red Teaming | [link](https://www.penligent.ai/hackinglabs/the-2026-ultimate-guide-to-ai-penetration-testing-the-era-of-agentic-red-teaming/) | 5 | High | 2026 | Practical guide: workflow tooling, agentic red teaming methodology, evidence-driven testing |
| 16.2 | Best AI Red Teaming and Adversarial Testing Tools in 2026 | [link](https://generalanalysis.com/guides/best-ai-red-teaming-tools) | 4 | High | 2026 | Tool comparison: General Analysis, PyRIT, garak, Inspect, DeepTeam for AI agent red teaming |
| 16.3 | Red Teaming Against Safety Frameworks (DeepTeam) | [link](https://www.trydeepteam.com/guides/guide-safety-frameworks) | 4 | High | 2026 | OWASP ASI 2026 application for agentic red teaming; safety framework bypass techniques |
| 16.4 | Red-Teaming LLMs 2026: A Practitioner's Guide | [link](https://datavlab.ai/post/red-teaming-llms-practitioner-guide-2026) | 4 | High | 2026 | OWASP vulnerabilities, five-phase methodology, tooling landscape for production AI security |
| 16.5 | Unit 42: Autonomous AI Cloud Attacks — Lessons Learned | [link](https://unit42.paloaltonetworks.com/autonomous-ai-cloud-attacks/) | 5 | High | 2026 | Multi-agent AI system autonomously attacking cloud; offensive agent research methodology (duplicate ref, high value) |

---

## 17. Identity & Access Management for AI Agents

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 17.1 | Zero Trust for AI Agents — Anthropic's Framework (Official) | [link](https://claude.com/blog/zero-trust-for-ai-agents) | 5 | High | 2026-05 | **Foundational**: tiered zero-trust architecture, eight-phase deployment, credential isolation, memory protection |
| 17.2 | Zero Trust for AI Agents — Varonis Implementation Guide | [link](https://www.varonis.com/blog/zero-trust-for-ai-agents) | 4 | Medium | 2026 | Practical enforcement of Anthropic's framework; enterprise deployment guidance |
| 17.3 | Zero Trust Architecture for Agentic AI in 2026 (Zentera) | [link](https://www.zentera.net/blog/zero-trust-architecture-for-agentic-ai) | 4 | High | 2026 | Every AI agent as untrusted principal; micro-segmentation, continuous verification architecture |
| 17.4 | Zero-Trust AI Agents: Why Credential Isolation Matters | [link](https://cloudradix.com/blog/zero-trust-ai-agents-credential-isolation-2026/) | 4 | High | 2026 | Agent-specific credentials with minimum-privilege scope; isolation from execution environment |
| 17.5 | AI Agent Security Beyond IAM — Risk After Authentication (Penligent) | [link](https://www.penligent.ai/hackinglabs/ai-agent-security-beyond-iam-why-the-real-risk-starts-after-authentication/) | 4 | High | 2026 | CSA's Agentic Trust Framework analysis; post-authentication runtime risk model |
| 17.6 | The OWASP Agentic Top 10 2026: What It Means for NHI (Entro Security) | [link](https://entro.security/blog/the-owasp-agentic-top-10-2026-what-it-means-for-ai-agents-and-non-human-identities/) | 4 | Medium | 2026 | Non-human identity perspective on OWASP Agentic Top 10; agents amplify existing IAM vulnerabilities |

---

## 18. Frontier Model Forum & Industry Consortia

| # | Title | URL | Technical Depth | Relevance | Date | Summary |
|---|-------|-----|:---:|:---:|:---:|--------|
| 18.1 | Emerging Security Practices for AI Agents (Frontier Model Forum) | [link](https://www.frontiermodelforum.org/issue-briefs/emerging-security-practices-for-ai-agents/) | 4 | High | 2026 | Industry consortium briefing: "lethal trifecta" framework for AI agent risk; multi-company security practices |

---

## Top Picks — Must-Read (Technical Depth ≥ 5 & High Relevance)

1. **[5.3] Trust No Skill** — Unit 42's integrity verification for AI agent supply chains
2. **[5.5] Autonomous AI Cloud Attacks** — Unit 42's multi-agent offensive research
3. **[5.6] Double Agents in Vertex AI** — GCP overprivileged agent vulnerability
4. **[4.2] Malicious Coding Agent Skills** — Datadog's supply chain research on coding agents
5. **[4.3] MCP SQL Injection** — Datadog's MCP server security audit
6. **[6.1] Agentic Tool Chain Attacks** — CrowdStrike's exploitation taxonomy
7. **[7.1] OWASP Agentic Top 10 2026** — The canonical risk framework
8. **[8.5] MCP by Design: RCE** — CSA's systemic MCP vulnerability disclosure
9. **[10.2] Attack and Defense Landscape of Agentic AI** — arXiv survey
10. **[10.3] Systematic Survey of Threats and Defenses** — arXiv comprehensive survey
11. **[10.4] OpenClaw Security Analysis** — 470-advisory vulnerability taxonomy
12. **[10.5] Execute-Only Agents** — Architectural prompt injection defense
13. **[10.6] Caging the Agents** — Zero trust architecture formalization
14. **[17.1] Anthropic Zero Trust for AI Agents** — Foundational industry framework
15. **[12.2] GPT-RED** — OpenAI's AI hacker +复旦AgentCyberRange靶场
16. **[11.1] AI Agent安全治理分水岭** — Anthropic白皮书中文深度解读

---

## Key Themes & Gaps Identified

### Covered Well
- **Agent Identity & IAM**: CrowdStrike CI, Cisco maturity model, Entra Agent ID — well-covered
- **OWASP Agentic Top 10**: Rich ecosystem of English & Chinese interpretations
- **Supply Chain Security**: MCP vulnerabilities, skill integrity, PyPI attacks — excellent coverage
- **Zero Trust Architectures**: Anthropic framework + multiple implementations
- **Prompt Injection**: Well-documented with real-world "in the wild" cases
- **Academic Surveys**: Multiple comprehensive arXiv/IEEE surveys available

### Less Covered (Gaps)
- **AI agent sandboxing isolation**: Few deep technical articles on sandbox internals
- **Multi-agent collusion attacks**: Mentioned in academic surveys but few practical deep dives
- **Agent-to-agent communication security**: Beyond MCP protocol analysis, limited material
- **Agent-specific SIEM/SOAR integration**: Runtime monitoring exists but deep threat detection articles sparse
- **Chinese offensive agent research**: Limited FreeBuf content; 安全客 has good depth but narrow focus

---

*Curated: July 2026 | Sources searched: VentureBeat, Cloudflare Blog, Wiz, Datadog Security Labs, Unit 42, CrowdStrike, OWASP GenAI, CSA, NIST, Gravitee, arXiv/IEEE, 知乎专栏, 安全客, FreeBuf*
