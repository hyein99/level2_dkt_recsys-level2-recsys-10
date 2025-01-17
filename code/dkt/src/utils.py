import os
import random

import numpy as np
import torch


def setSeeds(seed=42):

    # 랜덤 시드를 설정하여 매 코드를 실행할 때마다 동일한 결과를 얻게 합니다.
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)

    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    
    torch.backends.cudnn.deterministic = True
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
