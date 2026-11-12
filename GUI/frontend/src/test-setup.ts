import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// findBy*/waitFor 默认只等 1 s，并行负载下首次渲染会超出；调用处显式传入的 timeout 仍优先。
configure({ asyncUtilTimeout: 5_000 });
