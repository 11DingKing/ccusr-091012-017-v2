# 监管物资保管服务

该项目为监管仓、证物室和受控物资保管点提供服务端 API，覆盖人员授权、物资分类、批次登记、收发记录、审批、预警、审计日志与统计报表。数据保存在 SQLite，所有测试和接口验收均可在单个 Linux 应用容器内离线完成。

## 运行环境

- Python 3.11
- Django REST Framework
- SQLite

## 安装与初始化

```bash
python -m pip install -r backend/requirements.txt
cd backend
python manage.py migrate --run-syncdb
```

## 测试

```bash
cd backend
pytest -q
```

## 编译检查

```bash
python -m compileall -q backend
```

## API 验收

```bash
cd backend
python manage.py migrate --run-syncdb
python manage.py shell -c "from rest_framework.test import APIClient; from apps.authentication.models import User; u=User.objects.create_user('smoke','safe-pass',role='admin'); c=APIClient(); r=c.post('/api/auth/login/',{'username':'smoke','password':'safe-pass'},format='json'); print(r.status_code, bool(r.json()['data']['token']))"
```

## 容器

```bash
docker build -t custody-service .
docker run --rm custody-service
```

## 会计期间、封账与版本

为解决"月底确认收发汇总后，迟到补录改变已签字数字、复核无法重现"的问题，收发记录按
**业务日期**（`business_date`，缺省取录入日期）归属到 `YYYY-MM` 会计期间，并提供
试算、封账、重开审批与更正版本机制。

### 状态与版本

- 期间状态：`open`（开放）→ `trial`（试算中）→ `closed`（已封账）；
- **试算**只固化试算快照供核对，不落版本、不锁账；试算后数据若再变化，封账被拒绝并要求重新试算；
- **封账**生成不可变版本快照（首次为 v1），固化输入范围（日期起止、单据 ID 与完整明细行）、
  汇总规则（规则标识、版本、口径）和汇总摘要（收发笔数/数量、按货物明细）；
- **重复封账幂等**，返回同一版本，不产生新版本；
- **封账期间任何角色都不能增改删该月收发单据或签署该月出库单**（统一返回 403），
  更正必须先申请重开；普通用户不能试算/封账/审批（403）；
- **重开获批**后期间回到开放并把当前版本加 1，此后变更均带新版本号；再封账生成新版本，
  旧版本快照永久保留、随时可重现；
- 并发签署/封账/重开在事务内对关键行加锁复核，SQLite 通过 `BEGIN IMMEDIATE` 串行化写事务，
  并发请求结果确定：首次生效，其余得到 409。

### 收发单据

入库/出库均支持 `business_date`（跨月补录按业务发生日落账）：

```
POST   /api/stock-in/                 # 入库登记（封账月 403）
PUT    /api/stock-in/<id>/            # 修改
DELETE /api/stock-in/<id>/
POST   /api/stock-out/                # 出库申请
PUT    /api/stock-out/<id>/           # 仅待审批单可改
DELETE /api/stock-out/<id>/
POST   /api/stock-out/<id>/sign/      # 审批签署 {"approved": true|false}
POST   /api/stock-out/<id>/complete/  # 已批准单完成发放
GET    /api/stock-in/?period=YYYY-MM[&version=N]
GET    /api/stock-out/?period=YYYY-MM[&version=N]
```

### 期间、封账与重开接口

```
GET  /api/periods/                          # 期间列表
GET  /api/periods/YYYY-MM/                  # 期间状态
POST /api/periods/YYYY-MM/trial/            # 试算（管理员）
POST /api/periods/YYYY-MM/close/            # 封账，返回版本快照（管理员）
GET  /api/periods/YYYY-MM/summary/          # 汇总：默认当前版本（封账返快照，未封账返实时）
GET  /api/periods/YYYY-MM/summary/?version=N# 追溯历史版本（结果可重现）
GET  /api/periods/YYYY-MM/versions/         # 已封账版本列表
GET  /api/periods/YYYY-MM/versions/N/       # 指定版本快照（输入范围/规则/明细）
GET  /api/periods/YYYY-MM/audit-logs/       # 试算/封账/重开/单据变更审计
POST /api/reopen-requests/                  # 申请重开 {"period": "...", "reason": "..."}
GET  /api/reopen-requests/?period=YYYY-MM   # 重开申请列表
POST /api/reopen-requests/<id>/decide/      # 审批 {"approved": true|false}（管理员）
```

日报/月报（`/api/daily-report/`、仪表盘、每日报表任务）统一按业务日期归属，
跨月补录计入实际发生的日期与月份。

