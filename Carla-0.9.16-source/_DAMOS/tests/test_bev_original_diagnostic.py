from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from bev_original_diagnostic import confusion,metrics,prepare_ground_truth


class OriginalDiagnosticTests(unittest.TestCase):
    def test_confusion_uses_only_valid_ground_truth(self):
        target = np.array([[8,8,255],[7,4,11]])
        predicted = np.array([[8,7,4],[7,4,4]])
        mask = np.array([[1,1,1],[0,1,1]],bool)
        matrix = confusion(predicted,target,mask)
        self.assertEqual(matrix.sum(),4)
        self.assertEqual(matrix[8,8],1)
        self.assertEqual(matrix[8,7],1)
        self.assertEqual(matrix[7].sum(),0)
        self.assertEqual(matrix[11,4],1)

    def test_iou_counts_false_positives_and_false_negatives(self):
        matrix = np.zeros((17,17),np.int64)
        matrix[8,8],matrix[8,11],matrix[11,8] = 2,1,1
        report = metrics(matrix)
        self.assertEqual(report["object"]["iou"],.5)
        self.assertEqual(report["object"]["gt_cells"],3)
        self.assertIsNone(report["pedestrian"]["iou"])

    def test_ground_truth_densification_is_idempotent(self):
        calls = []
        def densify(bev,mask):
            calls.append(1)
            return bev+1,mask
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root/"agent"/"npz"/"frame.npz"
            path.parent.mkdir(parents=True)
            np.savez_compressed(path,bev_cls_future=np.zeros((1,2,2),np.uint8),
                bev_mask_future=np.ones((1,2,2),np.uint8),densify_pending=np.array(True))
            core = SimpleNamespace(densify_ground=densify)
            prepare_ground_truth(root,core)
            prepare_ground_truth(root,core)
            self.assertEqual(len(calls),1)
            with np.load(path) as data:
                self.assertNotIn("densify_pending",data.files)
                self.assertTrue((data["bev_cls_future"]==1).all())


if __name__=="__main__":
    unittest.main()
