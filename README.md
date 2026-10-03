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

## 月结封账与版本化（试算 / 封账 / 重开 / 更正版本）

财务月底确认收发汇总后，可对所属月份发起封账；封账会冻结**输入范围、汇总规则和汇总摘要**，之后任何迟到补录都不会改变已签字的数字，下一次复核可原样重现。

- 收发记录新增 `business_date`（业务日期）字段，迟到补录按业务实际发生日归月（跨月业务归属确定）。
- 封账/会签期间，普通用户（以及任何调用方）不能新增、修改、删除所属月份的入库/出库记录，接口返回 `409`。
- 封账采用管理员会签：应签署人全部签署后自动完成封账；重复发起、重复签署由数据库唯一约束保证结果确定（一成一冲突）。
- 确需调整时提交**重开申请**，管理员审批通过后旧版本标记为 `reopened` 并开启新的工作版本；此后所有补录/更正只进入新版本，历史版本快照永久保留、可追溯但不可改。
- 查询默认返回**当前版本**，可通过 `version=N` 追溯任意旧版本，`version=all` 返回全部版本。

### 主要接口（均需认证，`/api/` 前缀）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `accounting/trial/?period=YYYY-MM` | 试算：实时汇总，不落库；重开后含与上一版差异 |
| GET | `accounting/periods/YYYY-MM/` | 月份封账状态 |
| POST | `accounting/closures/` | 管理员发起封账（body: `{"period": "2026-09"}`） |
| GET | `accounting/closures/?period=YYYY-MM` | 版本列表（含每版冻结快照，用于追溯） |
| POST | `accounting/closures/<id>/sign/` | 应签署人会签 |
| POST | `accounting/reopen-requests/` | 提交重开申请（body: `period`, `reason`） |
| GET | `accounting/reopen-requests/?period=&status=` | 重开申请列表 |
| POST | `accounting/reopen-requests/<id>/decide/` | 管理员审批（body: `{"action": "approved"}`） |
| GET | `accounting/records/?period=&version=current|all|N&type=all|stock_in|stock_out` | 版本化收发记录 |

### 典型流程

```bash
# 1. 试算
curl -H "Authorization: Bearer $TOKEN" "$BASE/api/accounting/trial/?period=2026-09"
# 2. 管理员发起封账 -> 应签署人逐一会签 -> 集齐后 sealed
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"period":"2026-09"}' "$BASE/api/accounting/closures/"
curl -X POST -H "Authorization: Bearer $TOKEN" "$BASE/api/accounting/closures/<id>/sign/"
# 3. 封账后同月补录被拒（409）
# 4. 需要更正：申请重开 -> 管理员批准 -> 在新版本补录 -> 重新封账
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"period":"2026-09","reason":"月底漏登一笔入库"}' "$BASE/api/accounting/reopen-requests/"
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"action":"approved","reply":"同意更正"}' "$BASE/api/accounting/reopen-requests/<id>/decide/"
# 5. 查询默认当前版本；追溯旧版本加 version=1
curl -H "Authorization: Bearer $TOKEN" "$BASE/api/accounting/records/?period=2026-09&version=1"
```
