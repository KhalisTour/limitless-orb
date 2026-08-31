"""
CP (Canonical Polyadic) tensor decomposition model for HR prediction.

Learns latent batter × pitcher × pitch-family × zone × count × park
interaction modes from Statcast pitch-level data collapsed to per-PA.

score = sum_r [ a_r(batter) * b_r(pitcher) * c_r(pitch) * d_r(zone) * e_r(count) * g_r(park) ]
P(HR|PA) = sigmoid(bias + score)

Fit via L-BFGS-B (scipy.optimize) on logistic loss + L2 regularization.
Stronger reg on high-dimensional factors (batter, pitcher) to control overfitting.
Early stopping restores best validation AUC parameters.
"""

import json
import numpy as np
from scipy.optimize import minimize


PITCH_FAMILIES = {
    "FF": "fastball", "SI": "fastball", "FC": "fastball",
    "SL": "breaking", "CU": "breaking", "KC": "breaking",
    "CS": "breaking", "ST": "breaking", "SV": "breaking",
    "CH": "offspeed", "FS": "offspeed",
    "KN": "offspeed",
}

FAMILY_ORDER = ["fastball", "breaking", "offspeed"]

ZONE_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14]

COUNT_ORDER = [
    (0, 0), (0, 1), (0, 2),
    (1, 0), (1, 1), (1, 2),
    (2, 0), (2, 1), (2, 2),
    (3, 0), (3, 1), (3, 2),
]


def _sigmoid(x):
    x = np.clip(x, -30, 30)
    return 1.0 / (1.0 + np.exp(-x))


class CPContactModel:
    """CP decomposition model for P(HR|PA).

    Parameters
    ----------
    rank : int
        Number of latent components (R).
    reg : float
        Base L2 regularization strength.
    reg_player_mult : float
        Multiplier applied to reg for batter and pitcher embeddings.
    max_iter : int
        Maximum L-BFGS-B iterations.
    seed : int
        Random seed.
    """

    def __init__(self, rank=5, reg=1e-3, reg_player_mult=10.0,
                 max_iter=300, seed=42):
        self.rank = rank
        self.reg = reg
        self.reg_player_mult = reg_player_mult
        self.max_iter = max_iter
        self.seed = seed

        self.bias_ = 0.0
        self.embeddings_ = {}
        self.vocabs_ = {}
        self.factor_names_ = [
            "batter", "pitcher", "pitch_family", "zone", "count", "park"
        ]
        self.train_losses_ = []
        self._factor_regs_ = {}

    def _build_vocab(self, X):
        self.vocabs_ = {}
        for i, name in enumerate(self.factor_names_):
            vals = sorted(set(X[:, i]))
            self.vocabs_[name] = {v: idx for idx, v in enumerate(vals)}

    def _setup_regs(self):
        high_dim = {"batter", "pitcher"}
        for name in self.factor_names_:
            if name in high_dim:
                self._factor_regs_[name] = self.reg * self.reg_player_mult
            else:
                self._factor_regs_[name] = self.reg

    def _encode(self, X):
        n = X.shape[0]
        encoded = np.zeros((n, len(self.factor_names_)), dtype=np.int32)
        for i, name in enumerate(self.factor_names_):
            vocab = self.vocabs_[name]
            for j in range(n):
                encoded[j, i] = vocab.get(X[j, i], -1)
        return encoded

    def _pack(self):
        parts = [np.array([self.bias_])]
        for name in self.factor_names_:
            parts.append(self.embeddings_[name].ravel())
        return np.concatenate(parts)

    def _unpack(self, theta):
        self.bias_ = theta[0]
        offset = 1
        for name in self.factor_names_:
            n_vals = len(self.vocabs_[name])
            size = n_vals * self.rank
            self.embeddings_[name] = theta[offset:offset + size].reshape(n_vals, self.rank)
            offset += size

    def _score_encoded(self, X_enc):
        n = X_enc.shape[0]
        product = np.ones((n, self.rank))
        for i, name in enumerate(self.factor_names_):
            indices = X_enc[:, i]
            emb = self.embeddings_[name]
            mask = indices >= 0
            vecs = np.zeros((n, self.rank))
            if mask.any():
                vecs[mask] = emb[indices[mask]]
            product *= vecs
        return self.bias_ + product.sum(axis=1)

    def _loss_and_grad(self, theta, X_enc, y):
        self._unpack(theta)
        n = len(y)

        scores = self._score_encoded(X_enc)
        preds = _sigmoid(scores)
        residuals = preds - y

        loss = float(np.sum(
            -y * np.log(preds + 1e-15) - (1 - y) * np.log(1 - preds + 1e-15)
        )) / n

        for name in self.factor_names_:
            lam = self._factor_regs_[name]
            loss += lam * np.sum(self.embeddings_[name] ** 2) / n

        grad_parts = [np.array([residuals.mean()])]

        factor_vecs = []
        product_all = np.ones((n, self.rank))
        for i, name in enumerate(self.factor_names_):
            indices = X_enc[:, i]
            emb = self.embeddings_[name]
            mask = indices >= 0
            vecs = np.zeros((n, self.rank))
            if mask.any():
                vecs[mask] = emb[indices[mask]]
            factor_vecs.append(vecs)
            product_all *= vecs

        for i, name in enumerate(self.factor_names_):
            emb = self.embeddings_[name]
            indices = X_enc[:, i]
            vecs_i = factor_vecs[i]
            lam = self._factor_regs_[name]

            safe = np.where(np.abs(vecs_i) > 1e-12, vecs_i, 1.0)
            cofactor = product_all / safe

            grad_emb = np.zeros_like(emb)
            sample_grad = residuals[:, None] * cofactor / n

            mask = indices >= 0
            valid_idx = indices[mask]
            np.add.at(grad_emb, valid_idx, sample_grad[mask])
            grad_emb += 2 * lam * emb / n

            grad_parts.append(grad_emb.ravel())

        grad = np.concatenate(grad_parts)
        return loss, grad

    def fit(self, X, y, X_val=None, y_val=None, verbose=True):
        """Fit the model via L-BFGS-B with early stopping on validation AUC."""
        X = np.asarray(X, dtype=object)
        y = np.asarray(y, dtype=np.float64)
        rng = np.random.RandomState(self.seed)

        self._build_vocab(X)
        self._setup_regs()

        base_rate = y.mean()
        self.bias_ = np.log(base_rate / (1 - base_rate))

        scale = 1.0 / (self.rank ** (1.0 / 6.0))
        for name in self.factor_names_:
            n_vals = len(self.vocabs_[name])
            self.embeddings_[name] = scale + rng.normal(0, 0.01, size=(n_vals, self.rank))

        if verbose:
            sizes = {k: len(v) for k, v in self.vocabs_.items()}
            n_params = 1 + sum(len(v) * self.rank for v in self.vocabs_.values())
            print(f"[CP R={self.rank}] vocab sizes: {sizes}")
            print(f"[CP R={self.rank}] n_train={len(y)}, HR_rate={base_rate:.4f}, "
                  f"n_params={n_params:,}, reg={self.reg}, "
                  f"reg_player={self.reg * self.reg_player_mult:.4f}")

        X_enc = self._encode(X)
        X_val_enc = None
        if X_val is not None:
            X_val_arr = np.asarray(X_val, dtype=object)
            X_val_enc = self._encode(X_val_arr)

        theta0 = self._pack()

        best_auc = -1.0
        best_theta = theta0.copy()
        patience_counter = [0]
        patience = 50
        iteration_count = [0]

        def callback(theta):
            iteration_count[0] += 1
            if X_val_enc is None or y_val is None:
                return
            if iteration_count[0] % 10 != 0:
                return

            nonlocal best_auc, best_theta
            self._unpack(theta)
            scores_val = self._score_encoded(X_val_enc)
            preds_val = _sigmoid(scores_val)

            from sklearn.metrics import roc_auc_score
            try:
                auc = roc_auc_score(y_val, preds_val)
            except ValueError:
                return

            if verbose and iteration_count[0] % 20 == 0:
                scores_tr = self._score_encoded(X_enc)
                preds_tr = _sigmoid(scores_tr)
                tr_loss = -np.mean(y * np.log(preds_tr + 1e-15) + (1 - y) * np.log(1 - preds_tr + 1e-15))
                print(f"[CP R={self.rank}] iter {iteration_count[0]:>4d}  "
                      f"loss={tr_loss:.5f}  val_auc={auc:.4f}")

            if auc > best_auc:
                best_auc = auc
                best_theta = theta.copy()
                patience_counter[0] = 0
            else:
                patience_counter[0] += 10

        result = minimize(
            self._loss_and_grad,
            theta0,
            args=(X_enc, y),
            method="L-BFGS-B",
            jac=True,
            options={"maxiter": self.max_iter, "disp": False, "ftol": 1e-9},
            callback=callback,
        )

        if X_val is not None and y_val is not None and best_auc > 0:
            self._unpack(best_theta)
            if verbose:
                print(f"[CP R={self.rank}] restored best val_auc={best_auc:.4f} "
                      f"(iter ~{iteration_count[0]})")
        else:
            self._unpack(result.x)

        scores = self._score_encoded(X_enc)
        preds = _sigmoid(scores)
        final_loss = -np.mean(y * np.log(preds + 1e-15) + (1 - y) * np.log(1 - preds + 1e-15))
        self.train_losses_ = [float(final_loss)]

        if verbose:
            print(f"[CP R={self.rank}] final train_loss={final_loss:.5f}, "
                  f"L-BFGS iters={result.nit}")

        return self

    def predict_proba(self, X):
        X = np.asarray(X, dtype=object)
        X_enc = self._encode(X)
        scores = self._score_encoded(X_enc)
        return _sigmoid(scores)

    def get_embeddings(self):
        result = {}
        for name in self.factor_names_:
            inv_vocab = {idx: val for val, idx in self.vocabs_[name].items()}
            emb = self.embeddings_[name]
            result[name] = {
                inv_vocab[i]: emb[i].tolist() for i in range(len(emb))
            }
        return result

    def get_mode_loadings(self, component):
        result = {}
        for name in self.factor_names_:
            inv_vocab = {idx: val for val, idx in self.vocabs_[name].items()}
            emb = self.embeddings_[name]
            vals = [(inv_vocab[i], float(emb[i, component]))
                    for i in range(len(emb))]
            vals.sort(key=lambda x: abs(x[1]), reverse=True)
            result[name] = vals
        return result

    def component_scores(self, X):
        X = np.asarray(X, dtype=object)
        X_enc = self._encode(X)
        n = X_enc.shape[0]
        product = np.ones((n, self.rank))
        for i, name in enumerate(self.factor_names_):
            indices = X_enc[:, i]
            emb = self.embeddings_[name]
            mask = indices >= 0
            vecs = np.zeros((n, self.rank))
            if mask.any():
                vecs[mask] = emb[indices[mask]]
            product *= vecs
        return product

    def save(self, path):
        data = {
            "rank": self.rank,
            "bias": float(self.bias_),
            "reg": self.reg,
            "reg_player_mult": self.reg_player_mult,
            "vocabs": {k: {str(kk): vv for kk, vv in v.items()}
                       for k, v in self.vocabs_.items()},
            "embeddings": {k: v.tolist() for k, v in self.embeddings_.items()},
            "train_losses": self.train_losses_,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path):
        with open(path) as f:
            data = json.load(f)
        model = cls(rank=data["rank"])
        model.bias_ = data["bias"]
        model.reg = data.get("reg", 1e-3)
        model.reg_player_mult = data.get("reg_player_mult", 10.0)
        model.vocabs_ = {}
        for k, v in data["vocabs"].items():
            restored = {}
            for kk, vv in v.items():
                try:
                    restored[int(kk)] = vv
                except ValueError:
                    try:
                        restored[float(kk)] = vv
                    except ValueError:
                        restored[kk] = vv
            model.vocabs_[k] = restored
        model.embeddings_ = {k: np.array(v) for k, v in data["embeddings"].items()}
        model.train_losses_ = data.get("train_losses", [])
        return model
