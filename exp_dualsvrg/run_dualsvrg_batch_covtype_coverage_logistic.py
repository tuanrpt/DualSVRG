from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from __future__ import division
from __future__ import print_function
from __future__ import absolute_import

from male.models.kernel.dualsvrg import DualSVRG
from male.test_template import main_func, resolve_conflict_params

choice_default = 2


def create_obj_func(params):
    if choice_default == 0:
        default_params = {
            'w_regular': 0.1,
            'rf_dim': 400,
            'step_size': 0.5,
            'x_kernel_width': 0.0001,
            'params_kernel_width': 15,
            'num_iters': 500,
            'num_samples_params': 100,
            'batch_size': 100,
            'freq_calc_metrics': 1,

        }
    else:
        default_params = {
            'cache_size': 50,
            'freq_update_full_model': 100,
            'num_epochs': 3,
        }
    default_params = resolve_conflict_params(params, default_params)
    # print('Default params:', default_params)

    learner = DualSVRG(
        **params,
        **default_params,
    )
    return learner


def main_test(run_exp=False):
    params_gridsearch = {
        'rf_dim': [1600],
        'regular_param': [1.07570927967064E-07],
        'gamma': [4.0],
        'oracle': ['coverage'],
        'coverage_radius': [3.0],
        'loss_func': ['logistic'],
    }
    attribute_names = (
        'gamma', 'regular_param', 'learning_rate_scale', 'num_epochs', 'cache_size',
        'oracle', 'loss_func', 'core_max', 'coverage_radius', 'model_name', 'batch_size')

    main_func(
        create_obj_func,
        choice_default=choice_default,
        dataset_default='covtype',
        params_gridsearch=params_gridsearch,
        attribute_names=attribute_names,
        num_workers=4,
        file_config=None,
        run_exp=run_exp,
        freq_predict_display=10,
    )


if __name__ == '__main__':
    # pytest.main([__file__])
    main_test(run_exp=True)
