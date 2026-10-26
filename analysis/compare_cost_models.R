# Compare three cost models on identical cross-validation folds (optional R appendix; the app never needs R).
#
#   Rscript analysis/compare_cost_models.R          # full run (about 10-20 minutes, mostly the Bayesian fits)
#   Rscript analysis/compare_cost_models.R --fast   # one repeat, fewer posterior draws (for checking the script)
#
# Models (all on log budget, same cleaned data as the app: analysis/cost_training_data.csv):
#   current      fixed effects lm(y ~ scope + borough) with the app's per-scope empirical ranges
#   hierarchical lme4::lmer(y ~ 1 + (1 | scope) + (1 | borough)): scope and borough as random effects
#   bayesian     rstanarm::stan_lmer, same structure, posterior predictive ranges
# Each model is scored with its own ("native") ranges and with split-conformal ranges (Mondrian by scope:
# calibrated within each scope that has enough calibration projects, pooled otherwise).
#
# Winner rule (agreed with the project owner): a new model replaces the app's model only if it is more
# accurate (paired test on squared errors, p < 0.05) AND its 50% and 80% ranges cover within 3 points of
# target AND its 50% range is no wider than the current model's. Results go to analysis/; if a model wins,
# analysis/cost_model_estimates.csv holds its estimate for every scope x borough, which the app reads.

suppressPackageStartupMessages({
  library(lme4); library(rstanarm); library(dplyr); library(readr); library(tidyr)
  library(ggplot2); library(jsonlite); library(patchwork)
})
options(mc.cores = 2)
args <- commandArgs(trailingOnly = TRUE)
FAST <- "--fast" %in% args
here <- dirname(normalizePath(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))))
set.seed(20261026)

K <- 10
REPEATS <- if (FAST) 1 else 2
DRAWS <- if (FAST) 400 else 1000
POOLING <- 20          # same as the app: weight n / (n + 20) on a scope's own spread
MIN_CAL <- 8           # conformal: calibrate a scope on its own only with at least this many calibration projects

d <- read_csv(file.path(here, "cost_training_data.csv"), show_col_types = FALSE) |>
  mutate(y = log(total_budget), scope = factor(scope_label), borough = factor(borough))
cat(sprintf("%d projects, %d scopes, %d boroughs\n", nrow(d), nlevels(d$scope), nlevels(d$borough)))

# ---------- the three models: fit on train, return center and native ranges for test ----------

app_ranges <- function(resid, scope) {
  # The app's rule: per-scope quantiles shrunk toward the pooled ones, never narrower than the scope's own.
  pooled <- quantile(resid, c(.1, .25, .5, .75, .9))
  lapply(split(resid, scope), function(r) {
    w <- length(r) / (length(r) + POOLING)
    own <- quantile(r, c(.1, .25, .5, .75, .9))
    s <- w * own + (1 - w) * pooled
    c(lo80 = min(s[1], own[1]), lo50 = min(s[2], own[2]), mid = unname(s[3]), hi50 = max(s[4], own[4]),
      hi80 = max(s[5], own[5]))
  })
}

predict_current <- function(train, test) {
  fit <- lm(y ~ scope + borough, data = train)
  q <- app_ranges(residuals(fit), train$scope)
  m <- predict(fit, newdata = test)
  Q <- do.call(rbind, q[as.character(test$scope)])
  # center = the app's "typical" (median project); mean_center = the regression's mean prediction
  data.frame(center = m + Q[, "mid"], mean_center = m, lo50 = m + Q[, "lo50"], hi50 = m + Q[, "hi50"],
             lo80 = m + Q[, "lo80"], hi80 = m + Q[, "hi80"])
}

predict_hier <- function(train, test) {
  fit <- suppressMessages(lmer(y ~ 1 + (1 | scope) + (1 | borough), data = train))
  m <- predict(fit, newdata = test, allow.new.levels = TRUE)
  s <- sigma(fit)
  data.frame(center = m, mean_center = m, lo50 = m + qnorm(.25) * s, hi50 = m + qnorm(.75) * s,
             lo80 = m + qnorm(.10) * s, hi80 = m + qnorm(.90) * s)
}

predict_bayes <- function(train, test) {
  fit <- suppressWarnings(stan_lmer(y ~ 1 + (1 | scope) + (1 | borough), data = train, chains = 2,
                                    iter = if (FAST) 600 else 1000, refresh = 0, seed = 1))
  pp <- posterior_predict(fit, newdata = test, draws = DRAWS)
  q <- apply(pp, 2, quantile, probs = c(.1, .25, .5, .75, .9))
  data.frame(center = q[3, ], mean_center = colMeans(pp), lo50 = q[2, ], hi50 = q[4, ], lo80 = q[1, ], hi80 = q[5, ])
}

MODELS <- list(current = predict_current, hierarchical = predict_hier, bayesian = predict_bayes)

# ---------- split-conformal ranges around a model's center (Mondrian by scope) ----------

conformal <- function(predict_fn, train, test) {
  cal_idx <- unlist(lapply(split(seq_len(nrow(train)), train$scope), function(i) i[sample.int(length(i)) <=
                                                                                     max(1, floor(length(i) / 4))]))
  fit_part <- train[-cal_idx, ]; cal <- train[cal_idx, ]
  p_cal <- predict_fn(fit_part, cal)
  p_test <- predict_fn(fit_part, test)
  a <- abs(cal$y - p_cal$center)
  qa <- function(x, level) { n <- length(x); sort(x)[min(n, ceiling((n + 1) * level))] }
  pooled <- c(q50 = qa(a, .5), q80 = qa(a, .8))
  per <- lapply(split(a, cal$scope), function(x) if (length(x) >= MIN_CAL) c(q50 = qa(x, .5), q80 = qa(x, .8)) else pooled)
  Q <- do.call(rbind, per[as.character(test$scope)])
  data.frame(center = p_test$center, mean_center = p_test$mean_center,
             lo50 = p_test$center - Q[, "q50"], hi50 = p_test$center + Q[, "q50"],
             lo80 = p_test$center - Q[, "q80"], hi80 = p_test$center + Q[, "q80"])
}

# ---------- cross-validation: identical stratified folds for every model ----------

rows <- list()
for (rep in seq_len(REPEATS)) {
  fold <- integer(nrow(d))
  for (idx in split(seq_len(nrow(d)), d$scope)) fold[idx] <- (sample.int(length(idx)) - 1) %% K + 1
  for (f in seq_len(K)) {
    train <- d[fold != f, ]; test <- d[fold == f, ]
    for (m in names(MODELS)) {
      for (ranges in c("native", "conformal")) {
        p <- if (ranges == "native") MODELS[[m]](train, test) else conformal(MODELS[[m]], train, test)
        rows[[length(rows) + 1]] <- cbind(test[, c("fms_id", "scope", "borough", "y")], p, model = m,
                                          ranges = ranges, rep = rep, fold = f)
      }
    }
    cat(sprintf("repeat %d fold %d done\n", rep, f))
  }
}
cv <- bind_rows(rows) |>
  # Accuracy (R², RMSE, paired tests) uses each model's mean prediction, so a model reporting the median
  # project (as the app does, by design) is not penalized by squared-error scoring; % error uses the center.
  mutate(err = y - mean_center, err_center = y - center, in50 = y >= lo50 & y <= hi50, in80 = y >= lo80 & y <= hi80,
         width50 = exp(hi50 - lo50), width80 = exp(hi80 - lo80))
write_csv(cv, file.path(here, "cost_model_cv_predictions.csv"))

sst <- sum((d$y - mean(d$y))^2) * REPEATS
summary_tbl <- cv |> group_by(model, ranges) |>
  summarise(r2_out_of_sample = 1 - sum(err^2) / sst, rmse_log = sqrt(mean(err^2)),
            median_abs_pct_error = median(abs(exp(-err_center) - 1)), within_2x = mean(abs(err_center) < log(2)),
            coverage50 = mean(in50), coverage80 = mean(in80), width50 = mean(width50), width80 = mean(width80),
            .groups = "drop")
per_scope <- cv |> group_by(model, ranges, scope) |>
  summarise(n = n() / REPEATS, coverage50 = mean(in50), width50 = mean(width50), .groups = "drop")
print(as.data.frame(summary_tbl), digits = 3)

# Paired accuracy tests against the current model (same projects, same folds; centers do not depend on ranges)
base <- cv |> filter(model == "current", ranges == "native") |> arrange(rep, fms_id) |> pull(err)
tests <- lapply(c("hierarchical", "bayesian"), function(m) {
  e <- cv |> filter(model == m, ranges == "native") |> arrange(rep, fms_id) |> pull(err)
  tt <- t.test(e^2, base^2, paired = TRUE)
  wx <- suppressWarnings(wilcox.test(e^2, base^2, paired = TRUE))
  data.frame(model = m, mean_sq_error_change = mean(e^2 - base^2), paired_t_p = tt$p.value, wilcoxon_p = wx$p.value)
})
tests <- bind_rows(tests)
print(tests, digits = 3)

# ---------- winner rule ----------
cur <- summary_tbl |> filter(model == "current", ranges == "native")
cands <- summary_tbl |> filter(!(model == "current" & ranges == "native")) |>
  left_join(tests |> select(model, mean_sq_error_change, paired_t_p), by = "model") |>
  mutate(more_accurate = !is.na(paired_t_p) & paired_t_p < 0.05 & mean_sq_error_change < 0,
         honest_ranges = abs(coverage50 - .5) <= .03 & abs(coverage80 - .8) <= .03,
         no_wider = width50 <= cur$width50,
         wins = more_accurate & honest_ranges & no_wider)
winner <- cands |> filter(wins) |> arrange(rmse_log, width50) |> slice_head(n = 1)
decision <- if (nrow(winner)) sprintf("%s model with %s ranges replaces the current model", winner$model, winner$ranges) else
  "No new model met all three conditions; the app keeps the current model"
cat("\nDECISION:", decision, "\n")

# ---------- full-data fits: effects for the report, estimates for the app if a model wins ----------
fit_lm <- lm(y ~ scope + borough, data = d)
fit_h <- suppressMessages(lmer(y ~ 1 + (1 | scope) + (1 | borough), data = d))
fit_b <- suppressWarnings(stan_lmer(y ~ 1 + (1 | scope) + (1 | borough), data = d, chains = 2,
                                    iter = if (FAST) 600 else 2000, refresh = 0, seed = 1))
grid <- expand.grid(scope = levels(d$scope), borough = levels(d$borough))
grid$current <- predict(fit_lm, grid); grid$hierarchical <- predict(fit_h, grid)
pp <- posterior_predict(fit_b, newdata = grid, draws = DRAWS)
grid$bayesian <- apply(pp, 2, median)
effects <- grid |> pivot_longer(c(current, hierarchical, bayesian), names_to = "model", values_to = "log_pred") |>
  group_by(model, scope) |> summarise(typical = exp(mean(log_pred)), .groups = "drop")
bayes_var <- as.data.frame(VarCorr(fit_b)); hier_var <- as.data.frame(VarCorr(fit_h))

if (nrow(winner)) {
  # Estimates for every scope x borough from the winning model, with its ranges.
  if (winner$ranges == "native") {
    est <- if (winner$model == "bayesian") {
      qq <- apply(pp, 2, quantile, probs = c(.1, .25, .5, .75, .9))
      data.frame(grid[, c("scope", "borough")], typical = exp(qq[3, ]), low = exp(qq[2, ]), high = exp(qq[4, ]),
                 low80 = exp(qq[1, ]), high80 = exp(qq[5, ]))
    } else {
      p <- MODELS[[winner$model]](d, grid)
      data.frame(grid[, c("scope", "borough")], typical = exp(p$center), low = exp(p$lo50), high = exp(p$hi50),
                 low80 = exp(p$lo80), high80 = exp(p$hi80))
    }
  } else {
    p <- conformal(MODELS[[winner$model]], d, grid)
    est <- data.frame(grid[, c("scope", "borough")], typical = exp(p$center), low = exp(p$lo50), high = exp(p$hi50),
                      low80 = exp(p$lo80), high80 = exp(p$hi80))
  }
  est$model <- paste(winner$model, winner$ranges)
  write_csv(est, file.path(here, "cost_model_estimates.csv"))
} else if (file.exists(file.path(here, "cost_model_estimates.csv"))) {
  file.remove(file.path(here, "cost_model_estimates.csv"))
}

# ---------- charts ----------
theme_ce <- theme_minimal(base_size = 12) + theme(plot.title = element_text(face = "bold"), legend.position = "bottom")
pal <- c(current = "#000000", hierarchical = "#103FEF", bayesian = "#FFB320")
g1 <- ggplot(summary_tbl, aes(width50, coverage50, color = model, shape = ranges)) +
  geom_hline(yintercept = .5, linetype = "dashed", color = "#777") + geom_point(size = 4) +
  scale_color_manual(values = pal) + scale_y_continuous(labels = scales::percent) +
  labs(title = "Honest and narrow?", subtitle = "Share of held-out projects inside the 50% range vs. range width",
       x = "Average 50% range width (high / low)", y = "Coverage of 50% range", color = NULL, shape = NULL) + theme_ce
g2 <- ggplot(per_scope |> filter(ranges == "native"), aes(coverage50, reorder(scope, n), color = model)) +
  geom_vline(xintercept = .5, linetype = "dashed", color = "#777") + geom_point(size = 3, position = position_dodge(.5)) +
  scale_color_manual(values = pal) + scale_x_continuous(labels = scales::percent) +
  labs(title = "Coverage by scope (own ranges)", x = "Coverage of 50% range", y = NULL, color = NULL) + theme_ce
g3 <- ggplot(effects, aes(typical / 1e6, reorder(scope, typical), color = model)) +
  geom_point(size = 3, position = position_dodge(.5)) + scale_color_manual(values = pal) + scale_x_log10() +
  labs(title = "Typical budget by scope", subtitle = "Averaged over boroughs",
       x = "Typical budget ($ millions, log scale)", y = NULL, color = NULL) + theme_ce
best <- if (nrow(winner)) winner$model else "current"
g4 <- ggplot(cv |> filter(model == best, ranges == "native", rep == 1), aes(exp(center) / 1e6, exp(y) / 1e6)) +
  geom_abline(color = "#777", linetype = "dashed") + geom_point(alpha = .35, color = "#103FEF") +
  scale_x_log10() + scale_y_log10() +
  labs(title = sprintf("Predicted vs. actual (%s, held-out)", best), x = "Predicted ($M)", y = "Actual budget ($M)") + theme_ce
ggsave(file.path(here, "cost_model_comparison.png"), (g1 | g3) / (g2 | g4), width = 13, height = 10, dpi = 150)

# ---------- write-up ----------
fmt <- function(x, d = 3) formatC(x, digits = d, format = "f")
md <- c("# Cost model comparison (R appendix)", "",
        sprintf("Same cleaned data as the app (%d projects, %d scopes). %d x %d-fold cross-validation with identical folds for every model. Reproduce with `Rscript analysis/compare_cost_models.R`.",
                nrow(d), nlevels(d$scope), REPEATS, K), "",
        "| Model | Ranges | Out-of-sample R² | RMSE (log) | Median abs. % error | Within 2x | 50% coverage | 80% coverage | 50% width | 80% width |",
        "|---|---|---|---|---|---|---|---|---|---|",
        with(summary_tbl, sprintf("| %s | %s | %s | %s | %s%% | %s%% | %s%% | %s%% | %sx | %sx |", model, ranges,
                                  fmt(r2_out_of_sample), fmt(rmse_log), fmt(100 * median_abs_pct_error, 0),
                                  fmt(100 * within_2x, 0), fmt(100 * coverage50, 1), fmt(100 * coverage80, 1),
                                  fmt(width50, 1), fmt(width80, 1))), "",
        "**Accuracy vs. the current model** (paired tests on squared errors of the same held-out projects):", "",
        with(tests, sprintf("- %s: change in mean squared error %+.4f (paired t p = %s; Wilcoxon p = %s)", model,
                            mean_sq_error_change, fmt(paired_t_p), fmt(wilcoxon_p))), "",
        "**Variance components** (log scale, standard deviations):", "",
        sprintf("- Hierarchical: %s", paste(sprintf("%s %s", hier_var$grp, fmt(hier_var$sdcor)), collapse = "; ")),
        sprintf("- Bayesian (posterior): %s", paste(sprintf("%s %s", bayes_var$grp, fmt(bayes_var$sdcor)), collapse = "; ")), "",
        "**Winner rule:** more accurate (p < 0.05), 50% and 80% coverage within 3 points of target, and a 50% range no wider than the current model's.", "",
        "| Model | Ranges | More accurate | Honest ranges | No wider | Wins |", "|---|---|---|---|---|---|",
        with(cands, sprintf("| %s | %s | %s | %s | %s | %s |", model, ranges, more_accurate, honest_ranges, no_wider, wins)), "",
        sprintf("**Decision:** %s.", decision), "",
        "![Comparison charts](cost_model_comparison.png)")
writeLines(md, file.path(here, "cost_model_comparison.md"))
write_json(list(summary = summary_tbl, per_scope = per_scope, tests = tests, candidates = cands, decision = decision,
                winner = if (nrow(winner)) winner else NULL),
           file.path(here, "cost_model_comparison.json"), auto_unbox = TRUE, digits = 6, pretty = TRUE)
cat("Wrote analysis/cost_model_comparison.{md,json,png}\n")
