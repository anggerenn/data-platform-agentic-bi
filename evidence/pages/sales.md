---
title: Sales Dashboard
---
```sql daily_sales
select * from analytics.daily_sales
```

## Overview

<BigValue 
    data={daily_sales} 
    value=revenue 
    title="Total Revenue"
    fmt=usd
/>

<BigValue 
    data={daily_sales} 
    value=order_count 
    title="Total Orders"
/>

## Revenue by Category

<BarChart 
    data={daily_sales} 
    x=category 
    y=revenue 
    title="Revenue by Category"
/>

## Daily Revenue Trend

<LineChart 
    data={daily_sales} 
    x=order_date 
    y=revenue 
    title="Daily Revenue"
/>