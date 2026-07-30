# Work orders

The implementation lead creates one directory per delegated assignment:

```text
.agents/work-orders/WP-###-short-name/
├── contract.json
├── prompt.md
├── context.md
├── acceptance.md
├── report.md
├── report.json
└── leader-review.md
```

Use `.agents/templates/work-order/` as the source. A worker must not edit another work order.
