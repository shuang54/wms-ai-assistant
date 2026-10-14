"""用户认证域（Phase 7 Recovery M1 — 用户身份与认证地基）。

本包只放**认证原语**（密码哈希 / 不透明令牌），不放 HTTP / DB / 业务逻辑：

    password.py    scrypt 密码哈希与校验（stdlib only；无 bcrypt/passlib 依赖）
    tokens.py      不透明会话令牌生成与 SHA-256 哈希（stdlib secrets/hashlib）

边界（与 ``api/*_auth.py`` 的 X-API-Key 服务认证**并存不替代**）：

    * 本域 = **Human User Authentication**（浏览器用户；Cookie 会话）；
    * ``api/{agent,workflow,runtime}_*_auth`` = **Service-to-Service API Key**
      （服务端共享凭据 + 服务端项目作用域）；
    * 两者是不同的凭据类型，不能相互替代（见 M1 兼容性设计）。
"""
