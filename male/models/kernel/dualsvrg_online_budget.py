from __future__ import division
from __future__ import print_function
from __future__ import absolute_import

import numpy as np

from collections import deque

from ... import Model
from ...utils.disp_utils import visualize_classification_prediction

INF = 1e+8


class OnlineDualSVRG(Model):
    """Increment Gradient for Kernel Online Learning
    """
    ALWAYS_MOVE = 0
    BUDGET = 1
    COVERAGE = 2

    HINGE = 0
    LOGISTIC = 1

    def __init__(self,
                 model_name="IGKOL",
                 regular_param=0.1,
                 learning_rate_scale=1.0,
                 gamma=10,
                 rf_dim=400,
                 num_epochs=1,
                 batch_size=1,
                 cache_size=10,
                 freq_update_full_model=10,
                 oracle=BUDGET,
                 core_max=-1,
                 coverage_radius=-1,
                 loss_func=HINGE,
                 smooth_hinge_theta=0.5,
                 smooth_hinge_tau=0.5,
                 show_loss=True,
                 **kwargs):
        super(OnlineDualSVRG, self).__init__(model_name=model_name, **kwargs)
        self.regular_param = regular_param
        self.learning_rate_scale = learning_rate_scale
        self.gamma = gamma
        self.rf_dim = rf_dim
        self.num_epochs = num_epochs
        self.batch_size = batch_size
        self.cache_size = cache_size
        self.freq_update_full_model = freq_update_full_model
        self.oracle = oracle
        self.core_max = core_max
        self.coverage_radius = coverage_radius
        self.loss_func = loss_func
        self.smooth_hinge_theta = smooth_hinge_theta
        self.smooth_hinge_tau = smooth_hinge_tau
        self.show_loss = show_loss

    def _init(self):
        super(OnlineDualSVRG, self)._init()

        learning_rate_m = 1 if self.loss_func == 'hinge' else 1.0 / self.smooth_hinge_tau
        self.learning_rate = self.learning_rate_scale * np.minimum(
            1.0 / learning_rate_m,
            1.0 / np.abs(12*learning_rate_m-self.regular_param))

        self.omega = None
        self.w_cur_core = None
        self.num_core = None
        self.idx_core = None
        self.x_core = None
        self.w_cur_rf = None

        self.w_full_core = None
        self.w_full_rf = None

        self.idx_range = None

        self.mistake_rate = 0
        self.rf_2dim = self.rf_dim * 2
        self.rf_2dim_pad = self.rf_2dim + 1
        self.rf_scale = 1.0 / np.sqrt(self.rf_dim+1)

    def _calc_rf(self, xn):
        omega_x = np.matmul(xn, self.omega)
        xn_rf = np.ones(self.rf_2dim_pad)
        xn_rf[0:self.rf_dim] = np.cos(omega_x)
        xn_rf[self.rf_dim: self.rf_2dim] = np.sin(omega_x)
        xn_rf *= self.rf_scale
        return xn_rf

    def _get_grad_full(self, xn, xn_rf, yn, kn, kn_idx):
        yn_pred, wxn, xn_rf, wxn_rf, dist2_xn, kn, kn_idx, wxn_core = self._predict_one_given_w(
            self.w_full_rf, self.w_full_core, self.num_core, xn, xn_rf, kn, kn_idx
        )
        return self._get_grad(wxn, yn), kn, kn_idx

    def _get_grad(self, wxn, yn):
        idx_runner = np.argmax(wxn[np.arange(self.num_classes) != yn])
        idx_runner += (idx_runner >= yn)
        wxn_runner = wxn[idx_runner]
        wxn_true = wxn[yn]
        o = wxn_true - wxn_runner

        if self.loss_func == 'hinge':
            if o < 1-self.smooth_hinge_tau:
                loss = 1 - o - 0.5 * self.smooth_hinge_tau
                grad = -1
            elif o <= 1:
                loss = (0.5 / self.smooth_hinge_tau) * ((1 - o)**2)
                grad = 1.0 / self.smooth_hinge_tau
            else:
                loss = 0
                grad = 0
        elif self.loss_func == 'logistic':
            exp_minus_o = np.exp(-o)
            loss = np.log(1+exp_minus_o)
            grad = - exp_minus_o / (exp_minus_o + 1)
        else:
            raise NotImplementedError
        return grad, idx_runner, loss

    def _get_dist2(self, xn, idx):
        dist2 = np.sum(
            (self.x_[idx, :] - xn) ** 2, axis=1)
        return dist2

    def _oracle_always(self, dist2_xn):
        return True

    def _oracle_budget(self, dist2_xn):
        return False

    def _oracle_coverage(self, dist2_xn):
        if np.any(dist2_xn < self.coverage_radius):
            return True
        else:
            return False

    def _predict_one(self, xn):
        return self._predict_one_given_w(self.w_cur_rf, self.w_cur_core, self.num_core, xn)

    def _predict_one_given_w(self, w_rf, w_core, num_core, xn, xn_rf=None, kn=None, kn_idx=None):
        if xn_rf is None:
            xn_rf = self._calc_rf(xn)
        wxn_rf = np.sum(w_rf * xn_rf, axis=1)

        dist2_xn = None  # CARE
        # if kn is None:
        #     # idx = np.sum(np.abs(w_core), axis=0) != 0
        #     idx = np.any(w_core != 0, axis=0)
        #     # idx = np.pad(idx, (0, w_core.shape[1] - num_core), mode='constant')
        #     dist2_xn = self._get_dist2(xn, self.idx_core[self.idx_range[idx]])
        #     kn = np.exp(-self.gamma * dist2_xn)
        # else:
        #     # idx = np.sum(np.abs(w_core), axis=0) != 0
        #     idx = np.any(w_core != 0, axis=0)
        #     # idx = np.pad(idx, (0, w_core.shape[1] - num_core), mode='constant')
        #     idx_app = idx & (idx ^ kn_idx)
        #     dist2_app = self._get_dist2(xn, self.idx_core[self.idx_range[idx_app]])
        #     kn_app = np.exp(-self.gamma * dist2_app)
        #     kn_full = np.zeros(len(idx))
        #     kn_full[kn_idx] = kn
        #     kn_full[idx_app] = kn_app
        #     kn = kn_full[idx]
        # # print('len core: {}'.format(np.sum(idx)))
        # wxn_core = np.sum(w_core[:, idx] * kn, axis=1)

        if kn is None:
            # idx = np.sum(np.abs(w_core[:, :num_core]), axis=0) != 0
            idx = np.any(w_core[:, :num_core] != 0, axis=0)
            dist2_xn = self._get_dist2(xn, self.idx_core[:num_core][idx])
            kn = np.exp(-self.gamma * dist2_xn)
        else:
            len_kn_idx = len(kn_idx)
            kn_idx = np.pad(kn_idx, (0, num_core-len_kn_idx), mode='constant')

            # idx = np.sum(np.abs(w_core[:, :num_core]), axis=0) != 0
            idx = np.any(w_core[:, :num_core] != 0, axis=0)
            idx_app = idx & (idx ^ kn_idx)
            dist2_app = self._get_dist2(xn, self.idx_core[:num_core][idx_app])
            kn_app = np.exp(-self.gamma * dist2_app)
            kn_full = np.zeros(num_core)
            kn_full[:num_core][kn_idx] = kn
            kn_full[:num_core][idx_app] = kn_app
            kn = kn_full[idx]

        idx_full = np.pad(idx, (0, w_core.shape[1] - num_core), mode='constant')

        wxn_core = np.sum(w_core[:, idx_full] * kn, axis=1)

        wxn = wxn_rf + wxn_core
        y_pred = np.argmax(wxn)
        return y_pred, wxn, xn_rf, wxn_rf, dist2_xn, kn, idx, wxn_core

    def _get_mean_loss(self, x, y):
        num_tests = x.shape[0]
        mean_loss = 0.0
        for nn in range(num_tests):
            xn = x[nn, :]
            yn = y[nn]
            yn_pred, wxn, xn_rf, wxn_rf, dist2_xn, kn, kn_idx, wxn_core = self._predict_one(xn)
            grad_cur, idx_cur_runner, loss_cur = self._get_grad(wxn, yn)
            mean_loss += np.maximum(0, loss_cur)
        mean_loss = mean_loss / num_tests
        return mean_loss

    def _fit_loop(self, x, y,
                  do_validation=False,
                  x_valid=None, y_valid=None,
                  callbacks=None, callback_metrics=None):
        self.x_ = x
        self.y_ = y

        print(self.oracle == 'budget')

        num_samples = x.shape[0]
        input_dim = x.shape[1]
        rf_dim = self.rf_dim
        rf_2dim_pad = self.rf_2dim_pad

        self.idx_range = np.arange(num_samples)

        self.omega = np.random.normal(0, self.gamma / 2, (input_dim, rf_dim))
        self.w_cur_core = np.zeros((self.num_classes, num_samples))
        self.idx_core = np.zeros(num_samples, dtype=int)
        self.chk_core = -np.ones(num_samples, dtype=int)
        self.chk_in_core = -np.ones(num_samples, dtype=int)
        self.num_core = 0

        self.w_cur_rf = np.zeros((self.num_classes, rf_2dim_pad))

        self.w_full_core = np.zeros((self.num_classes, num_samples))
        self.w_full_rf = np.zeros((self.num_classes, rf_2dim_pad))

        self.w_cur_core[0] = 0
        self.w_full_core[0] = 0
        self.idx_core[0] = 0
        self.chk_core[0] = 0
        self.chk_in_core[0] = 0
        self.num_core += 1

        grad_lst = deque()
        xnt_rf_lst = deque()
        grad_idx_lst = deque()
        k_lst = deque()
        k_idx_lst = deque()

        sum_grad_rf = np.zeros((self.num_classes, self.rf_2dim_pad))
        sum_grad_core = np.zeros((self.num_classes, num_samples))
        w_sum_rf = np.zeros((self.num_classes, self.rf_2dim_pad))
        w_sum_core = np.zeros((self.num_classes, num_samples))

        max_loop = num_samples
        move_decision = np.zeros(max_loop)
        idx_samples = np.random.permutation(num_samples)
        total_error = 0

        rf_lst = num_samples * [None]
        for n in range(max_loop):
            epoch_logs = {}
            callbacks.on_epoch_begin(self.epoch)
            if 'train_loss' in callback_metrics:
                mean_loss = self._get_mean_loss(x, y)
                epoch_logs['train_loss'] = mean_loss
            if 'valid_loss' in callback_metrics:
                y_valid_trans = self._transform_labels(y_valid)
                mean_loss = self._get_mean_loss(x_valid, y_valid_trans)
                epoch_logs['valid_loss'] = mean_loss

            nt = idx_samples[n]
            xnt = x[nt, :]
            ynt = y[nt]

            # predict
            ynt_pred, wxnt, xnt_rf, wxnt_rf, dist2_xnt, knt, knt_idx, wxnt_core = self._predict_one(xnt)
            total_error += ynt_pred != ynt
            self.mistake_rate = total_error / (n + 1)
            if 'mistake_rate' in callback_metrics:
                epoch_logs['mistake_rate'] = self.mistake_rate
            if (n % 1000) == 0:
                print('n={}; mistake_rate={}'.format(n, self.mistake_rate))

            rf_lst[nt] = xnt_rf

            grad_cur, idx_cur_runner, loss_cur = self._get_grad(wxnt, ynt)
            (grad_full, idx_full_runner, loss_full), knt, knt_idx = self._get_grad_full(xnt, xnt_rf, ynt, knt, knt_idx)

            move_decision[n] = {
                'budget': self._oracle_budget,
                OnlineDualSVRG.COVERAGE: self._oracle_coverage,
                OnlineDualSVRG.ALWAYS_MOVE: self._oracle_always,
            }[self.oracle](dist2_xnt)

            if len(grad_lst) > self.cache_size - 1:
                xnt_rf_lst.popleft()
                idx_pop = grad_idx_lst.popleft()
                k_lst.popleft()
                k_idx_lst.popleft()

                grad_tmp = grad_lst.popleft()

                if len(grad_tmp.shape) == 2:
                    sum_grad_rf -= grad_tmp
                else:
                    sum_grad_core[:, self.chk_core[idx_pop]] -= grad_tmp

            xnt_rf_lst.append(xnt_rf)
            grad_idx_lst.append(nt)
            k_lst.append(knt)
            k_idx_lst.append(knt_idx)

            if move_decision[n]:
                # print('approx')
                # approximate
                vnt_rf_cur = np.zeros((self.num_classes, self.rf_2dim_pad))
                vnt_rf_cur[ynt, :] = grad_cur * xnt_rf
                vnt_rf_cur[idx_cur_runner, :] = -grad_cur * xnt_rf

                vnt_rf_full = np.zeros((self.num_classes, self.rf_2dim_pad))
                vnt_rf_full[ynt, :] = grad_full * xnt_rf
                vnt_rf_full[idx_full_runner, :] = -grad_full * xnt_rf

                sum_grad_rf += vnt_rf_full
                grad_lst.append(vnt_rf_full)

                vnt = vnt_rf_cur - vnt_rf_full + sum_grad_rf / len(grad_lst)
                self.w_cur_rf = \
                    (self.w_cur_rf - self.learning_rate * vnt) / \
                    (self.learning_rate * self.regular_param + 1)

                self.w_cur_core[:, :self.num_core] += \
                    - sum_grad_core[:, :self.num_core] * self.learning_rate \
                    / (len(grad_lst) * (self.learning_rate * self.regular_param + 1))
            else:
                # add to core
                vnt_core_cur = np.zeros(self.num_classes)
                vnt_core_cur[ynt] = grad_cur
                vnt_core_cur[idx_cur_runner] = -grad_cur

                vnt_core_full = np.zeros(self.num_classes)
                vnt_core_full[ynt] = grad_full
                vnt_core_full[idx_full_runner] = -grad_full

                if self.chk_in_core[nt] < 0:
                    self.num_core += 1
                    self.idx_core[self.num_core - 1] = nt
                    self.chk_core[nt] = self.num_core - 1
                    self.chk_in_core[nt] = self.num_core - 1
                    self.w_cur_core[:, self.num_core - 1] = np.zeros(self.num_classes)
                elif self.chk_core[nt] < 0:
                    self.chk_core[nt] = self.chk_in_core[nt]

                sum_grad_core[:, self.chk_core[nt]] += vnt_core_full
                grad_lst.append(vnt_core_full)

                self.w_cur_core[:, self.chk_core[nt]] += \
                    - (vnt_core_cur - vnt_core_full) * self.learning_rate \
                    / (self.learning_rate * self.regular_param + 1)
                # CARE when upgrade to BATCH SETTING += NOT =

                self.w_cur_core[:, :self.num_core] += \
                    - sum_grad_core[:, :self.num_core] * self.learning_rate \
                    / (len(grad_lst) * (self.learning_rate * self.regular_param + 1))

                self.w_cur_rf += -sum_grad_rf * self.learning_rate \
                    / (len(grad_lst) * (self.learning_rate * self.regular_param + 1))

            w_sum_rf += self.w_cur_rf
            w_sum_core[:, :self.num_core] += self.w_cur_core[:, :self.num_core]
            # print('num_core=', self.num_core)

            if self.oracle == 'budget':
                # w_value_tmp = np.sum(np.abs(self.w_cur_core), axis=0)
                w_idx_tmp = np.any(self.w_cur_core != 0, axis=0)
                if self.verbose > 0:
                    print('true core:', np.sum(w_value_tmp != 0))
                num_remove = np.sum(w_idx_tmp) - self.core_max + 1
                if (n+1) % self.freq_update_full_model != 0:
                    num_remove = 0
                else:
                    num_remove = num_remove
                if self.verbose > 0:
                    print('remove core:', num_remove)
                if num_remove > 0:
                    w_value_tmp = np.sum(np.abs(self.w_cur_core), axis=0)
                    w_value_tmp[~w_idx_tmp] = np.nan
                    idx_core_remove_lst = np.argsort(w_value_tmp)[:num_remove]
                    for i_remove in range(num_remove):
                        idx_core_remove = idx_core_remove_lst[i_remove]
                        nt_remove = self.idx_core[idx_core_remove]
                        xnt_rf_remove = rf_lst[nt_remove]
                        self.w_cur_rf += np.kron(self.w_cur_core[:, idx_core_remove], xnt_rf_remove).reshape(
                            (self.num_classes, self.rf_2dim_pad))
                        self.w_cur_core[:, idx_core_remove] = 0
                        # self.w_full_core[:, idx_core_remove] = 0
                        # print('Remove')
                        self.chk_core[nt_remove] = -1

            if (n+1) % self.freq_update_full_model == 0:
                self.w_full_rf = w_sum_rf / self.freq_update_full_model
                self.w_cur_rf = self.w_full_rf.copy()
                w_sum_rf = np.zeros((self.num_classes, self.rf_2dim_pad))

                self.w_full_core = w_sum_core / self.freq_update_full_model
                self.w_cur_core = self.w_full_core.copy()
                w_sum_core = np.zeros((self.num_classes, num_samples))

                sum_grad_rf = np.zeros((self.num_classes, self.rf_2dim_pad))
                sum_grad_core = np.zeros((self.num_classes, num_samples))
                grad_lst.clear()

                for i in range(n-self.cache_size+1, n+1):
                    if i < 0:
                        continue
                    it = idx_samples[i]
                    if it != grad_idx_lst[i-n+len(grad_idx_lst)-1]:
                        print('Error idx')
                    xit_tmp = x[it, :]
                    yit_tmp = y[it]
                    kit_tmp = k_lst.popleft()
                    kit_idx_tmp = k_idx_lst.popleft()
                    xit_rf = xnt_rf_lst.popleft()
                    (grad_full, idx_full_runner, loss_full), kit_tmp, kit_idx_tmp = self._get_grad_full(
                        xit_tmp, xit_rf, yit_tmp, kit_tmp, kit_idx_tmp)
                    k_lst.append(kit_tmp)
                    k_idx_lst.append(kit_idx_tmp)
                    # if move_decision[i]:
                    if self.chk_core[it] < 0:
                        vit_rf_full = np.zeros((self.num_classes, self.rf_2dim_pad))
                        vit_rf_full[ynt, :] = grad_full * xit_rf
                        vit_rf_full[idx_full_runner, :] = -grad_full * xit_rf

                        sum_grad_rf += vit_rf_full
                        grad_lst.append(vit_rf_full)
                    else:
                        vit_core_full = np.zeros(self.num_classes)
                        vit_core_full[yit_tmp] = grad_full
                        vit_core_full[idx_full_runner] = -grad_full

                        # if self.chk_core[nt] < 0:
                        #     self.num_core += 1
                        #     self.idx_core[self.num_core - 1] = nt
                        #     self.chk_core[nt] = self.num_core - 1
                        #     self.w_cur_core[:, self.num_core - 1] = np.zeros(self.num_classes)

                        sum_grad_core[:, self.chk_core[it]] += vit_core_full
                        grad_lst.append(vit_core_full)
                    xnt_rf_lst.append(xit_rf)

                if self.oracle == 'budget':
                    # w_value_tmp = np.sum(np.abs(self.w_cur_core), axis=0)
                    w_idx_tmp = np.any(self.w_cur_core != 0, axis=0)
                    if self.verbose > 0:
                        print('true core:', np.sum(w_value_tmp != 0))
                    num_remove = np.sum(w_idx_tmp) - self.core_max + 1
                    if (n + 1) % self.freq_update_full_model != 0:
                        num_remove = 0
                    else:
                        num_remove = num_remove
                    if self.verbose > 0:
                        print('remove core:', num_remove)
                    if num_remove > 0:
                        w_value_tmp = np.sum(np.abs(self.w_cur_core), axis=0)
                        w_value_tmp[~w_idx_tmp] = np.nan
                        idx_core_remove_lst = np.argsort(w_value_tmp)[:num_remove]
                        for i_remove in range(num_remove):
                            idx_core_remove = idx_core_remove_lst[i_remove]
                            nt_remove = self.idx_core[idx_core_remove]
                            xnt_rf_remove = rf_lst[nt_remove]
                            self.w_cur_rf += np.kron(self.w_cur_core[:, idx_core_remove], xnt_rf_remove).reshape(
                                (self.num_classes, self.rf_2dim_pad))
                            self.w_cur_core[:, idx_core_remove] = 0
                            # self.w_full_core[:, idx_core_remove] = 0
                            # print('Remove')
                            self.chk_core[nt_remove] = -1

            self.epoch += 1
            callbacks.on_epoch_end(self.epoch, epoch_logs)
        print()
        if self.verbose > 0:
            print('num_core=', np.sum(np.sum(np.abs(self.w_cur_core), axis=0) != 0))
        self.w_cur_core = self.w_full_core.copy()
        self.w_cur_rf = self.w_cur_rf.copy()

    def predict(self, x):
        y = np.zeros(x.shape[0], dtype=int)
        for n in range(x.shape[0]):
            y[n], _, _, _, _, _, _, _ = self._predict_one(x[n])
            y[n] = self._decode_labels(y[n])
        return y

    def display_prediction(self, **kwargs):
        visualize_classification_prediction(self, self.x_, self.y_, **kwargs)

    def display(self, param, **kwargs):
        if param == 'predict':
            self.display_prediction(**kwargs)
        else:
            raise NotImplementedError

    def get_params(self, deep=True):
        out = super(OnlineDualSVRG, self).get_params(deep=deep)
        param_names = OnlineDualSVRG._get_param_names()
        out.update(self._get_params(param_names=param_names, deep=deep))
        return out