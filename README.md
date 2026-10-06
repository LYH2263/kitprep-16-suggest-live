# KitPrep 中央厨房 BOM 备料

按菜品 BOM 展开订单行、合并同原料需求，对照库存计算缺料并生成备料单。

技术栈：Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL / Vue 3 / TypeScript / Vite

## 启动

```bash
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:5000 |
| API | http://localhost:10100 |
| API 文档 | http://localhost:10100/docs |
| Postgres | localhost:5451 |

健康检查：`GET http://localhost:10100/api/health`

## 使用说明

1. 在「菜品」「BOM」维护中央厨房出品与用料树。
2. 在「订单」「库存」确认当日需求与现有库存；库存页可直接修改结存。
3. 打开「备料单」点「刷新建议缺料」，建议列与右侧缺料贴来自同一份快照。
4. 核对无误后点「落单」：按需求全额扣减结存（扣成负数即为待采购量），落单结果与建议列逐项一致。
5. 改了结存或订单后，旧建议列立即作废，必须重新刷新才能落单；落单时若服务器重算与建议列不一致，会拒绝落单且库存不动。已落成的旧单冻结，不会被后续刷新或落单改动。
6. 在「缺料」查看当前快照中 need − stock 为正的原料。

## 开发与测试

```bash
docker compose exec api pytest -q
```
