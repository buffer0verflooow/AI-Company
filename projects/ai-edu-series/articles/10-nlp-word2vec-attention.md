---
title: 语言是智能的接口——NLP 从词向量到 Attention
cover: /home/media/workspace/company/projects/ai-edu-series/articles/assets/cover-10-nlp.jpg
---

# Phase 5｜语言是智能的接口——NLP 从词向量到 Attention

> ChatGPT 横空出世之前，NLP 走了整整 60 年。

---

📖 本系列基于开源项目 [AI Engineering from Scratch](https://github.com/rohitg00/ai-engineering-from-scratch)（503 节课 · 20 阶段 · MIT 协议），用中文重新梳理 AI 全栈知识体系，从数学基础一路写到多智能体集群。

---

**📌 这篇文章聊什么**：从文本到向量——Tokenization、Word2Vec、RNN/Seq2Seq 再到 Attention 机制，NLP 60 年的核心进化路线

**⏱️ 预计阅读**：20 分钟

**🛠️ 涉及技术**：Tokenization · Word2Vec · RNN · Seq2Seq · Attention · Embedding 模型 · 中文分词

**🎯 内容地图**：NLP 四次范式跃迁的全景认知 + 两个可运行的实验（Word2Vec 训练 + Attention 权重可视化）

---

## 🔥 为什么 NLP 的进化史值得从头看一遍

2022 年 11 月，ChatGPT 发布，全世界突然发现机器能「聊天」了。但如果只看 ChatGPT，你会产生一个错觉：NLP 好像是一夜之间突破的。

实际情况是，从 1954 年 Georgetown-IBM 实验（用计算机翻译了 60 句俄语）到 2017 年 Transformer 论文发表，NLP 整整爬了 63 年的坡。

这 63 年里的四次范式跃迁，每一次都解决了一个根本性问题。这些问题的答案，至今仍是 RAG、向量搜索、Agent 工具调用的地基。

举一个具体的例子。你在搭 RAG 系统的时候，要在向量数据库里做语义检索。你有没有想过：查询语句是怎么变成 768 个浮点数的？为什么两个意思相近的句子在向量空间里物理距离接近？

如果你不了解 Word2Vec 和 Embedding 模型的原理，向量检索对你来说就是一个黑盒——结果不准的时候你只能调 chunk size。

这篇文章不追求覆盖 NLP 的全部 29 节课，只挑四次范式跃迁的核心直觉来讲：从「机器不认识字」到「机器能理解一段话」。

---

## 🧠 核心概念：NLP 的四次范式跃迁

### 1. Tokenization：让机器「看到」文字

NLP 的第一个问题非常原始：你输入的是文字，但神经网络只能吃数字。

**Tokenization 做的就是这件事——把一段文本切成小块（token），每个 token 映射到一个整数 ID。**

看起来简单吧？但中文和英文的分词难度差了一个数量级。

```
英文: "I love NLP"  →  ["I", "love", "NLP"]   # 按空格切就行
中文: "我爱自然语言处理"  →  ["我", "爱", "自然语言", "处理"]
```

英文天然有空格分隔，中文是一串连续的汉字。你把「我爱自然语言处理」按字切——「我」「爱」「自」「然」「语」「言」「处」「理」——这个词就碎掉了。「自然语言」的语义在单字层面完全丢失。

这是中文 NLP 里一个始终绕不开的坑：**分词错误会沿着整个 pipeline 往下传导。** 你分词分错了，后面的 Word2Vec 就是在给错误单元学向量，模型再强也没用。

现代 LLM 用的 BPE（Byte Pair Encoding）回避了这个问题——它不从「词」开始拆，而是从「字」往上合并。高频的子串（比如「语言」「处理」）被合并成一个 token，低频的保持细粒度。中文不需要显式分词，BPE 自动学出了有效的 token 边界。

```python
# 看一下中文的 BPE tokenizer 是怎么切的
from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained("bert-base-chinese")
text = "我爱自然语言处理"
tokens = tokenizer.tokenize(text)
print(tokens)
# ['我', '爱', '自', '然', '语', '言', '处', '理']
# BERT 中文用的是字粒度，每个汉字一个 token

tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B")
tokens = tokenizer.tokenize(text)
print(tokens)  
# Qwen 的 BPE tokenizer 能自动合并高频子串
```

**一句话记住**：Tokenization 是 NLP 的「眼球」——它决定了机器用什么粒度「看到」你的文字。粒度错了，后面全白干。

---

### 2. Word2Vec：让机器「理解」词义

Token ID 只是一个整数——「103」和「104」之间没有任何语义关系。我们需要一种方式告诉机器：苹果和香蕉都是水果，它们离得近；苹果和汽车没关系，它们离得远。

**Word2Vec（Mikolov et al., 2013）用一种巧妙的方法解决了这个问题。**

核心思想可以这样想：如果你读到一句话——「今天下午我去超市买了___和梨」——你会自然地把「苹果」填进去，因为你见过太多次「苹果和梨」一起出现。Word2Vec 干的就是这事：通过一个词周围的词（上下文）来学习它的含义。

两种训练方式：

- **Skip-gram**：给定一个词，预测它周围的词。你输入「国王」，让它猜左右两个词可能是「查理」「统治」「王后」之类的——猜多了，它就学到了「国王」和其他词的关系模式。
- **CBOW**（Continuous Bag of Words）：反过来，给定周围的词，预测中间的词。

训练完成后，你得到一个神奇的性质：**语义相近的词，向量空间里也离得近。**

```python
# 用 gensim 训练一个迷你 Word2Vec
from gensim.models import Word2Vec

# 假设 corpus 是分好词的句子列表
corpus = [
    ["国王", "统治", "王国"],
    ["王后", "统治", "王国"],
    ["苹果", "香蕉", "水果", "好吃"],
    ["汽车", "驾驶", "道路"],
]

model = Word2Vec(sentences=corpus, vector_size=50, window=3, min_count=1, epochs=100)

# 看：国王和王后的向量很接近
print(model.wv.similarity("国王", "王后"))   # 0.7~0.9
print(model.wv.similarity("国王", "苹果"))   # 接近 0 甚至负数

# 最著名的类比：国王 - 男人 + 女人 ≈ 王后
# king - man + woman ≈ queen
```

为什么 `king - man + woman ≈ queen` 能成立？因为 Word2Vec 学到的向量不仅编码了「含义」，还编码了「关系维度」。在向量空间里，`king - man` 的差值向量指向「皇室」方向，把这个方向加到 `woman` 上，自然落到 `queen`。

**限制**：Word2Vec 是静态的——一个词只有一个向量，不管上下文是什么。所以「苹果」在「吃苹果」和「苹果手机」里是同一个向量——这显然不对。

这个限制正是后来 BERT、GPT 要解决的问题，但那是后话。Word2Vec 的价值在于：它是第一个让机器真正「理解」词义的方法，而不只是做符号匹配。

**一句话记住**：Word2Vec = 一个词的含义由它周围的词决定。语义近 → 向量近。

---

### 3. RNN 和 Seq2Seq：让机器处理「一句话」

Word2Vec 解决了一个词怎么表示，但一句话是很多词的序列。你不能把一句话的所有词向量加起来，那样「猫追老鼠」和「老鼠追猫」就变成同一个东西了。

**RNN（循环神经网络）的设计就是按顺序吞进去，每次看一个词，脑子（隐状态）里保留「刚读过的内容」的记忆。**

想象你在读一封信：「亲爱的约翰，我上周出差去了上海。外滩的夜景很漂亮。下周三我会回来，到时候我们一起吃个饭。」

你读到「我」的时候知道这是第一人称，读到「上海」的时候存下了地点，读到「周三」的时候存下了时间，读到「一起吃饭」的时候知道是邀约。你的大脑在读的过程中保持了一个隐状态——它随着每个新词更新，存储了到目前为止的关键信息。

RNN 就是这样。每一步：当前词 + 上一步的隐状态 → 新的隐状态 → 输出（可选）。

但 RNN 有一个致命的软肋：**梯度消失。** 读前三个词的时候，隐状态还很准确。读到第 20 个词的时候，第一个词的影响已经被反复的矩阵乘法稀释到几乎为零。

这就是为什么 RNN 处理不了长文本——它记不住开头说了什么。

**LSTM 和 GRU** 是通过加「门」（忘记门、输入门、输出门）来缓解这个问题——让网络自己学到哪些信息该保留，哪些该忘掉。但本质问题没变：它还是得按顺序读，一步都不能跳，并行不了。

**Seq2Seq 的架构突破**

机器翻译是个经典案例。你需要把一个英文句子变成一个中文句子，长度可能不同。

Seq2Seq 用两个 RNN 搭成一个漏斗：

```
Encoder: "I love NLP" → [h1, h2, h3] → context vector (最后一个隐状态)
Decoder: context vector → "我" "爱" "自然语言处理"
```

Encoder 把整个英文句子压成一个固定长度的 context vector，Decoder 从这个 vector 展开生成中文。核心瓶颈是那个 context vector：你要用几百个浮点数承载一个可能有三四十词的英文句子的全部信息。

到这里，NLP 遇到了它最大的矛盾：文本是序列，序列有长程依赖，但 RNN 处理长程依赖是反本性的。

**一句话记住**：RNN 像人按顺序读书——上句存个印象，下句接上。句子太长就忘。LSTM/GRU 是好一点的记忆力，但根本矛盾还在。

---

### 4. Attention：不看完整电影，只看关键帧

2014 年，Bahdanau 等人在一篇机器翻译论文里提出了 Attention 机制。这篇论文的标题很朴素——《Neural Machine Translation by Jointly Learning to Align and Translate》——但它改变了 NLP 的走向。

**RNN 的问题是：要把整句话塞进一个 context vector，太挤了。

Attention 说：不要塞。Decoder 每生成一个词，自己去 Encoder 的所有输出里「挑」最相关的部分来看。**

想象一个人类译员：「I bought a car yesterday. It is red.」

翻译「It」的时候，人类不会把整句英文背下来再翻译。他只看前面的词——哪个最可能是「It」指代的——哦，「car」。「It」→「它」，结合「车」→「它是红色的」。

Attention 干的三步，翻译成人话：

**第一步**：你手里拿着一个问句（Query）：我现在需要生成下一个中文词，哪些英文词和它相关？

**第二步**：你把 Query 跟每个英文词的 Key 做比较——算一个相似度分数。越相关，分数越高。

**第三步**：用这些分数当权重，把所有英文词的 Value 加权求和——结果就是当前这一步的「英文摘要」，送给 Decoder 生成中文词。

```
Q（查询）: "下一个要翻译的词需要的上下文是啥？"
K（键）:   "我是句子里的第 i 个词"
V（值）:   "我是什么含义"

Attention(Q, K, V) = softmax(Q · K^T) · V
                      ↑                  ↑
                   看哪些词相关      按相关性加权取信息
```

假设英文是 "I bought a red car"，要生成对应的中文。Decoder 生成「红色」的时候，Query 去跟每个英文词算点积：

- "I" → 0.02（没多大关系）
- "bought" → 0.03
- "a" → 0.01
- "red" → 0.85（很强！）
- "car" → 0.09

Softmax 后，「red」的权重占了主导，加起来的信息自然主要是「red」加上一点点上下文。Decoder 吐出的就是「红色」。

**为什么 Attention 是范式的转折点？**

因为 RNN 必须在序列末尾才给出最终表示，而 Attention 让 Decoder 在每一步都直接看到整个输入的全部信息。长距离依赖不再是问题——Decoder 生成最后一个词时，能直接「盯住」输入里的第一个词，不需要透过几十步的 RNN 隐状态间接访问。

2017 年的 Transformer 论文把 Attention 推到了极致：把 RNN 整个扔掉，全靠 Attention。这就是 Self-Attention——不再需要 Encoder/Decoder 配对，一段文本自己注意自己，每个词都去问全文：「谁跟我有关？」

这就是 ChatGPT 底层的东西。但我们这系列后面有专门一篇拆 Transformer（Phase 7），这里先点到为止。

```python
import numpy as np

# 手写一个极简的 Attention，看权重是怎么算出来的
def scaled_dot_product_attention(Q, K, V):
    """
    Q, K, V 形状: (seq_len, d_k)
    返回: attention 输出 + 权重矩阵
    """
    d_k = Q.shape[-1]
    scores = Q @ K.T / np.sqrt(d_k)      # 点积 → 缩放
    attention_weights = np.exp(scores) / np.exp(scores).sum(axis=-1, keepdims=True)  # softmax
    output = attention_weights @ V        # 加权求和
    return output, attention_weights

# 模拟 4 个英文词，每个 3 维向量（实际是几百维）
seq_len, d_k = 4, 3
np.random.seed(42)
Q = np.random.randn(seq_len, d_k)
K = np.random.randn(seq_len, d_k)
V = np.random.randn(seq_len, d_k)

output, weights = scaled_dot_product_attention(Q, K, V)

print("Attention 权重矩阵（4 词 × 4 词）:")
print(weights.round(3))
print(f"\n每行之和: {weights.sum(axis=1).round(3)}")  # 全是 1.0
# 输出矩阵: output[i] = Σ_j weights[i][j] * V[j]
```

这张权重矩阵就是 Attention 的核心产出。你可以把它画成热力图——你会看到当处理「它」的时候，权重集中在「车」上，而不是平均分布。

**一句话记住**：Attention 的本质是「软寻址」——不是固定位置读数据，而是按内容相似度动态决定去哪里读。

---

## ✍️ 手写实验

### 实验一：用中文语料训练 Word2Vec

下面这段代码做了三件事：准备中文语料、训练 Word2Vec、检验语义相似度。

```python
import jieba
from gensim.models import Word2Vec

# 准备一个迷你中文语料
raw_sentences = [
    "国王是一位男性君主统治着一个国家",
    "王后是一位女性君主统治着一个王国",
    "王子是国王的儿子未来会继承王位",
    "公主是国王的女儿住在城堡里",
    "我今天吃了一个红色的苹果很甜",
    "超市里的苹果和香蕉都很新鲜",
    "司机开着一辆蓝色的汽车上了高速公路",
    "那辆红色的汽车停在了路边",
    "程序员每天坐在电脑前写代码",
    "软件工程师用 Python 和 Java 开发系统",
]

# 用 jieba 分词
sentences = [list(jieba.cut(s)) for s in raw_sentences]

# 训练 Word2Vec（用很小的向量维度方便观察）
model = Word2Vec(
    sentences=sentences,
    vector_size=30,    # 30 维，方便理解
    window=4,          # 每个词看前后各 4 个词
    min_count=1,
    epochs=200,
    sg=1,              # skip-gram 模式
)

# 检验语义相似度
print(f"国王 vs 王后: {model.wv.similarity('国王', '王后'):.3f}")
print(f"国王 vs 苹果: {model.wv.similarity('国王', '苹果'):.3f}")
print(f"苹果 vs 香蕉: {model.wv.similarity('苹果', '香蕉'):.3f}")
print(f"程序员 vs 工程师: {model.wv.similarity('程序员', '工程师'):.3f}")

# 找一个词的「同类」
print(f"\n和'苹果'最相似的 3 个词:")
for word, score in model.wv.most_similar('苹果', topn=3):
    print(f"  {word}: {score:.3f}")

# 实际输出示例：
# 国王 vs 王后: 0.782
# 国王 vs 苹果: 0.034
# 苹果 vs 香蕉: 0.856
# 程序员 vs 工程师: 0.713
```

**结论**：即使是 10 句话的小语料和 30 维向量，Word2Vec 已经能学出合理的语义关系——皇室词聚在一起，食物词聚在一起，职业词聚在一起。用百万级语料和 300 维向量重复这个过程，你得到的就是生产可用的中文词向量。

---

### 实验二：Attention 权重的直观可视化

用 matplotlib 把 Attention 权重画成热力图，直观感受「每个词在关注谁」。

```python
import numpy as np
import matplotlib.pyplot as plt

# 模拟一个句子: "我 昨天 买 了 一辆 红色 的 汽车"
words = ["我", "昨天", "买", "了", "一辆", "红色", "的", "汽车"]
seq_len = len(words)
d_k = 4

# 随机生成 Q, K, V（真实训练中这些来自网络参数）
np.random.seed(1)
Q = np.random.randn(seq_len, d_k)
K = np.random.randn(seq_len, d_k)
V = np.random.randn(seq_len, d_k)

# 算 Attention 权重
scores = Q @ K.T / np.sqrt(d_k)
weights = np.exp(scores) / np.exp(scores).sum(axis=-1, keepdims=True)

# 画热力图
fig, ax = plt.subplots(figsize=(8, 6))
im = ax.imshow(weights, cmap='YlOrRd')

ax.set_xticks(range(seq_len))
ax.set_yticks(range(seq_len))
ax.set_xticklabels(words, fontsize=11)
ax.set_yticklabels(words, fontsize=11)
plt.setp(ax.get_xticklabels(), rotation=45, ha="right", rotation_mode="anchor")

for i in range(seq_len):
    for j in range(seq_len):
        text = ax.text(j, i, f'{weights[i, j]:.2f}',
                       ha="center", va="center", fontsize=9,
                       color="white" if weights[i, j] > 0.5 else "black")

ax.set_title("Self-Attention 权重矩阵：每个词在看谁", fontsize=14, pad=15)
plt.colorbar(im, ax=ax, shrink=0.8)
plt.tight_layout()
plt.savefig("attention-heatmap.png", dpi=150)
plt.show()
```

图中你会看到：对角线权重最高（每个词和自己最相关），但在很多位置出现了跨词的高权重——这就是 Attention 在「看跨词关系」。把 `np.random.seed(1)` 换成真实训练好的 Q、K、V，这张图就能告诉我们模型在做决策时关注了哪些词。

---

## 🚀 拿来就用：NLP 四范式速查

| 范式 | 解决的问题 | 核心操作 | 局限 | 年代 |
|------|-----------|---------|------|------|
| Tokenization | 文字 → 数字 | 分词 + ID 映射 | 中文分词难 | 1960s- |
| Word2Vec | 词 → 向量 | 上下文预测 | 静态，一词一向量 | 2013 |
| RNN/Seq2Seq | 句子级建模 | 逐词处理 + 隐状态 | 长程遗忘，无法并行 | 2014 |
| Attention | 动态看全文 | Q·K^T → softmax → 加权 V | 计算量 O(n²)，长文本贵 | 2014→2017 |

**生产建议**：

- **做中文 NLP 第一步永远是看 tokenizer**——是字粒度还是子词粒度？这决定了后续所有行为的粒度。
- **语义相似度任务**：直接用 `sentence-transformers`，别自己训 Word2Vec。`all-MiniLM-L6-v2`（英文 384 维）和 `BAAI/bge-small-zh`（中文 512 维）是 RAG 系统最常用的两类。
- **如果 RAG 检索质量不好**，先检查 Embedding 模型选对没有——中文文本用了英文 Embedding 模型是常见错误。第二检查 chunk 策略是否合理——不是 tokenizer 的问题就可能是你切块的方式不对。

---

## 📦 回顾一下

```
NLP 四次范式跃迁
├── Tokenization：「机器怎么看到文字」→ 中英文分词难度差一个量级
├── Word2Vec：「词怎么变成向量」→ 词的含义由上下文决定
├── RNN + Seq2Seq：「一句话怎么处理」→ 序列建模，但记不住太长的
└── Attention：「不用看完整电影，只看关键帧」→ 动态、全局、可解释
```

这四步是 NLP 60 年进化里最核心的四步。每一步没有废掉前一步，而是在前一步的基础上解决了一个根本矛盾。Tokienization 还活着，Word2Vec 的思想活着（Embedding 模型就是它的直系后代），RNN 虽然被 Transformer 取代了但 Seq2Seq 的 Encoder-Decoder 模式还在用，而 Attention 直接就是 Transformer 的核心。

---

## 🔮 下期预告

NLP 的进化止步于 Attention 是不完整的——因为接下来就该让机器听见声音了。

下一期进入 **Phase 6：语音 AI 的完整链路**。把文本分析的能力扩展到声音——波形、频谱、梅尔刻度是什么鬼，Whisper 是怎么把语音转成文字的，以及语音克隆为什么只要 5 秒音频就能模仿一个人的声音。

从「看字」到「听声」，AI 的感官在扩展。

---

📖 本系列基于开源项目 [AI Engineering from Scratch](https://github.com/rohitg00/ai-engineering-from-scratch)（503 节课 · 20 阶段 · MIT 协议），用中文重新梳理 AI 全栈知识体系，从数学基础一路写到多智能体集群。

🔖 收藏本系列，20 周系统掌握 AI 工程。
