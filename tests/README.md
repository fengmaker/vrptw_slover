# M0～M5 测试

在项目根目录运行：

```powershell
python -m pytest -q -p no:cacheprovider --basetemp=tests/.pytest-local
```

- `test_problem.py`：不可变输入、编号和字段约束、千分位舍入矩阵。
- `test_solomon.py`：全部 56 个原始 TXT、C101 字段、坏行与转换格式拒绝。
- `test_evaluate.py`：等待、截止边界、超载、回仓、覆盖、空路线车辆计数，以及 C101 外部路线重新验证。
- `test_construct.py`：C101 自研初始可行解、确定性、无法插入时的明确原因。
- `test_moves.py`：四种邻域、客户守恒、真实 C101 移动差分、不可行移动拒绝和局部最优停止。
- `test_solver.py`：C101 自研 10 车、容量下界和零迭代行为。
- `test_ils_fleet.py`：固定种子/迭代复现、零秒停止、参数验证。
- `test_report_cli.py`：五个输出文件、篡改自报成本后的独立验证、批量单例失败继续。

`fixtures/C101.sol` 来自 `PyVRP-main/results/solomon/C101.sol`。测试故意不读取该文件自报的 `Cost`。
